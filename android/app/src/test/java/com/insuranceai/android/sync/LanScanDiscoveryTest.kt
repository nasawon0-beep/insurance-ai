package com.insuranceai.android.sync

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LanScanDiscoveryTest {
    private val instance = "0123456789abcdef0123456789abcdef"
    private val leaf = "a".repeat(64)
    private val spki = "b".repeat(64)
    private val trust = ScanTrust(18443, "192.168.0.73", leaf, spki, instance)

    @Test
    fun scanStaysOnPrivateLanInterfacesAndAtMostSlash24() {
        val hosts = lanScanHostCandidates(
            listOf(
                LocalIpv4("wlan0", "172.20.10.2", 28), // 폰 핫스팟 /28
                LocalIpv4("rmnet_data0", "10.45.1.9", 8), // 셀룰러(CGNAT) 제외
                LocalIpv4("tun0", "10.8.0.2", 24), // VPN 제외
                LocalIpv4("ap0", "192.168.43.1", 16), // /16 → /24로 축소
                LocalIpv4("wlan1", "100.64.3.2", 24), // 사설 아님 제외
            ),
            exclude = setOf("192.168.43.10"),
        )
        assertTrue(hosts.all { it.startsWith("172.20.10.") || it.startsWith("192.168.43.") })
        assertEquals((1..14).map { "172.20.10.$it" }.filter { it != "172.20.10.2" }, hosts.filter { it.startsWith("172.20.10.") })
        val ap = hosts.filter { it.startsWith("192.168.43.") }
        assertEquals(252, ap.size) // .1(자기) .10(제외) .0 .255 빼고
        assertFalse("192.168.43.1" in ap || "192.168.43.0" in ap || "192.168.43.255" in ap || "192.168.43.10" in ap)
        assertTrue(hosts.size <= LAN_SCAN_MAX_HOSTS * 2)
        assertTrue(isLanInterfaceName("swlan0") && !isLanInterfaceName("rmnet0") && !isLanInterfaceName("ccmni1"))
    }

    @Test
    fun scanTrustRequiresHttpsBoundSpkiAndPcIdentity() {
        assertEquals(trust, scanTrustFrom("https://192.168.0.73:18443", leaf, spki, instance))
        assertNull(scanTrustFrom("http://192.168.0.73:18443", leaf, spki, instance))
        assertNull(scanTrustFrom("https://192.168.0.73:18443", leaf, null, instance)) // SPKI 미바인딩
        assertNull(scanTrustFrom("https://192.168.0.73:18443", leaf, spki, null))
        assertNull(scanTrustFrom("https://192.168.0.73:18443", "zz", spki, instance))
    }

    @Test
    fun onlyHostsThatPassPinnedTlsBecomeCandidatesAndNothingElseIsContacted() = runTest {
        val tlsProbed = mutableListOf<String>()
        val discovery = PinnedLanScanDiscovery(
            trust = { trust },
            locals = { listOf(LocalIpv4("wlan0", "172.20.10.2", 28)) },
            portOpen = { host, port, _ -> port == 18443 && host in setOf("172.20.10.5", "172.20.10.9") },
            pinnedTlsProbe = { host, _, t ->
                tlsProbed += host
                assertEquals(spki, t.spkiSha256)
                host == "172.20.10.9" // .5는 다른 TLS 서버(핀 불일치)
            },
        )
        val found = discovery.discover()
        assertEquals(listOf(DiscoveredPc("https://172.20.10.9:18443", instance)), found)
        assertEquals(setOf("172.20.10.5", "172.20.10.9"), tlsProbed.toSet())
    }

    @Test
    fun noTrustOrNoLanMeansNoScan() = runTest {
        var touched = 0
        val noTrust = PinnedLanScanDiscovery(
            trust = { null },
            locals = { listOf(LocalIpv4("wlan0", "172.20.10.2", 28)) },
            portOpen = { _, _, _ -> touched++; true },
            pinnedTlsProbe = { _, _, _ -> touched++; true },
        )
        assertTrue(noTrust.discover().isEmpty())
        val cellularOnly = PinnedLanScanDiscovery(
            trust = { trust },
            locals = { listOf(LocalIpv4("rmnet0", "10.1.2.3", 24)) },
            portOpen = { _, _, _ -> touched++; true },
            pinnedTlsProbe = { _, _, _ -> touched++; true },
        )
        assertTrue(cellularOnly.discover().isEmpty())
        assertEquals(0, touched)
    }

    @Test
    fun tlsProbesAreBounded() = runTest {
        var probes = 0
        val discovery = PinnedLanScanDiscovery(
            trust = { trust },
            locals = { listOf(LocalIpv4("wlan0", "192.168.5.20", 24)) },
            portOpen = { _, _, _ -> true }, // 모든 호스트가 같은 포트를 연 악조건
            pinnedTlsProbe = { _, _, _ -> probes++; false },
        )
        assertTrue(discovery.discover().isEmpty())
        assertTrue(probes <= LAN_SCAN_MAX_TLS_PROBES)
    }
}
