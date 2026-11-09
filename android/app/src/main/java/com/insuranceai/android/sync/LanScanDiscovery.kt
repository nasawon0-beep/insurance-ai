package com.insuranceai.android.sync

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.net.Inet4Address
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.Socket
import java.net.URI
import java.util.Collections

/**
 * NSD(mDNS)가 막힌 네트워크(폰 핫스팟, 멀티캐스트 차단 공유기)에서 쓰는 대체 탐색.
 *
 * 안전 조건
 * - 이 폰의 Wi-Fi/핫스팟/USB 테더링 인터페이스의 사설 IPv4 서브넷만(최대 /24, 셀룰러·VPN 제외).
 * - 저장된 PC 포트 하나만, 호스트·동시성·전체 시간 제한.
 * - 열린 포트에는 TLS 핸드셰이크만 한다: 이미 신뢰한 PC 인증서(leaf 또는 바인딩된 SPKI)와 SAN 주소 검증을
 *   통과한 주소만 후보가 된다. 이 단계에서는 토큰·요청 본문을 보내지 않는다.
 * - 후보는 기존 verifyAndUpdateDiscoveredAddress(같은 TLS 핀 + 기기·PC identity·미해결 충돌 확인)로만 확정된다.
 * - SPKI 바인딩이 없는 연결은 대체 탐색을 하지 않는다(주소가 바뀌면 leaf가 바뀌어 어차피 검증 불가).
 */
data class ScanTrust(
    val port: Int,
    val currentHost: String,
    val leafSha256: String,
    val spkiSha256: String,
    val pcInstanceId: String,
)

data class LocalIpv4(val interfaceName: String, val address: String, val prefixLength: Int)

private val LAN_INTERFACE_PREFIXES = listOf("wlan", "swlan", "softap", "ap", "eth", "rndis", "usb", "bt-pan", "ncm")
private val SHA256_HEX = Regex("[0-9a-f]{64}")
private val INSTANCE_HEX = Regex("[0-9a-f]{32}")

const val LAN_SCAN_MAX_HOSTS = 254
const val LAN_SCAN_CONCURRENCY = 32
const val LAN_SCAN_CONNECT_TIMEOUT_MS = 300
const val LAN_SCAN_TCP_DEADLINE_MS = 6_000L
const val LAN_SCAN_MAX_TLS_PROBES = 6
const val LAN_SCAN_TLS_TIMEOUT_MS = 1_500

fun isLanInterfaceName(name: String): Boolean {
    val lower = name.lowercase()
    return LAN_INTERFACE_PREFIXES.any { lower.startsWith(it) }
}

private fun ipv4ToInt(address: String): Int? {
    val parts = address.split('.')
    if (parts.size != 4) return null
    var value = 0
    for (part in parts) {
        val octet = part.toIntOrNull() ?: return null
        if (octet !in 0..255) return null
        value = (value shl 8) or octet
    }
    return value
}

private fun intToIpv4(value: Int): String =
    listOf(24, 16, 8, 0).joinToString(".") { ((value ushr it) and 0xff).toString() }

fun isPrivateLanIpv4(address: String): Boolean {
    val value = ipv4ToInt(address) ?: return false
    val a = (value ushr 24) and 0xff
    val b = (value ushr 16) and 0xff
    return a == 10 || (a == 172 && b in 16..31) || (a == 192 && b == 168)
}

/** LAN 인터페이스의 사설 서브넷에서 스캔할 호스트(자기 주소·네트워크·브로드캐스트·제외 주소 빼고, 인터페이스마다 최대 /24). */
fun lanScanHostCandidates(locals: List<LocalIpv4>, exclude: Set<String>): List<String> {
    val out = LinkedHashSet<String>()
    for (local in locals) {
        if (!isLanInterfaceName(local.interfaceName) || !isPrivateLanIpv4(local.address)) continue
        val own = ipv4ToInt(local.address) ?: continue
        val prefix = local.prefixLength.coerceIn(24, 30)
        val mask = if (prefix == 0) 0 else (-1 shl (32 - prefix))
        val network = own and mask
        val broadcast = network or mask.inv()
        var host = network + 1
        while (host < broadcast) {
            val text = intToIpv4(host)
            if (host != own && text !in exclude) out += text
            host++
        }
    }
    return out.take(LAN_SCAN_MAX_HOSTS * 2)
}

fun systemLocalIpv4(): List<LocalIpv4> = runCatching {
    Collections.list(NetworkInterface.getNetworkInterfaces()).flatMap { network ->
        if (!network.isUp || network.isLoopback || network.isVirtual) return@flatMap emptyList()
        network.interfaceAddresses.mapNotNull { entry ->
            val address = entry.address as? Inet4Address ?: return@mapNotNull null
            LocalIpv4(network.name, address.hostAddress ?: return@mapNotNull null, entry.networkPrefixLength.toInt())
        }
    }
}.getOrDefault(emptyList())

suspend fun tcpPortOpen(host: String, port: Int, timeoutMs: Int): Boolean = withContext(Dispatchers.IO) {
    runCatching { Socket().use { it.connect(InetSocketAddress(host, port), timeoutMs); true } }.getOrDefault(false)
}

/** 저장된 연결 정보에서 대체 탐색용 신뢰 값(토큰 제외)을 만든다. SPKI 바인딩·PC identity가 없으면 null. */
fun scanTrustFrom(
    endpoint: String?,
    leafSha256: String?,
    spkiSha256: String?,
    pcInstanceId: String?,
): ScanTrust? {
    val uri = runCatching { URI(endpoint ?: return null) }.getOrNull() ?: return null
    if (uri.scheme != "https" || uri.port !in 1..65535) return null
    val host = uri.host ?: return null
    val leaf = leafSha256?.lowercase()?.replace(":", "") ?: return null
    val spki = spkiSha256?.lowercase()?.replace(":", "") ?: return null
    if (!SHA256_HEX.matches(leaf) || !SHA256_HEX.matches(spki)) return null
    if (pcInstanceId == null || !INSTANCE_HEX.matches(pcInstanceId)) return null
    return ScanTrust(uri.port, host, leaf, spki, pcInstanceId)
}

class PinnedLanScanDiscovery(
    private val trust: () -> ScanTrust?,
    private val locals: () -> List<LocalIpv4> = ::systemLocalIpv4,
    private val portOpen: suspend (String, Int, Int) -> Boolean = ::tcpPortOpen,
    private val pinnedTlsProbe: suspend (String, Int, ScanTrust) -> Boolean = { host, port, t ->
        probePinnedTlsIdentity(host, port, t.leafSha256, t.spkiSha256, LAN_SCAN_TLS_TIMEOUT_MS)
    },
) : PcDiscovery {
    override suspend fun discover(): List<DiscoveredPc> {
        val t = runCatching { trust() }.getOrNull() ?: return emptyList()
        val hosts = lanScanHostCandidates(locals(), setOf(t.currentHost))
        if (hosts.isEmpty()) return emptyList()
        val open = Collections.synchronizedList(mutableListOf<String>())
        val gate = Semaphore(LAN_SCAN_CONCURRENCY)
        withTimeoutOrNull(LAN_SCAN_TCP_DEADLINE_MS) {
            coroutineScope {
                hosts.map { host ->
                    async {
                        gate.withPermit {
                            if (open.size < LAN_SCAN_MAX_TLS_PROBES && portOpen(host, t.port, LAN_SCAN_CONNECT_TIMEOUT_MS)) {
                                open += host
                            }
                        }
                    }
                }.awaitAll()
            }
        }
        val probes = synchronized(open) { open.take(LAN_SCAN_MAX_TLS_PROBES) }
        return probes
            .filter { host -> runCatching { pinnedTlsProbe(host, t.port, t) }.getOrDefault(false) }
            .map { host -> DiscoveredPc("https://$host:${t.port}", t.pcInstanceId) }
    }
}
