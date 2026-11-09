package com.insuranceai.android.sync

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.URL
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.security.cert.CertificateException
import java.util.concurrent.atomic.AtomicReference
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

class LanSyncHttpException(val statusCode: Int) : IllegalStateException("LAN sync HTTP $statusCode")

/**
 * 대체 탐색용: TLS 핸드셰이크만 하고 연결을 닫는다(HTTP 요청·토큰 전송 없음).
 * 이미 신뢰한 PC 인증서(leaf 핀 또는 바인딩된 SPKI)와 플랫폼 기본 hostname(SAN IP) 검증을 모두 통과해야 true.
 */
suspend fun probePinnedTlsIdentity(
    host: String,
    port: Int,
    leafSha256: String,
    spkiSha256: String?,
    timeoutMs: Int,
): Boolean = withContext(Dispatchers.IO) {
    runCatching {
        val peer = AtomicReference<X509Certificate>()
        val trust = object : X509TrustManager {
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) =
                throw CertificateException("Client authentication is not used")
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                val leaf = chain.firstOrNull() ?: throw CertificateException("Missing server certificate")
                validatePinnedServerCertificate(leaf, leafSha256, spkiSha256)
                peer.set(leaf)
            }
        }
        val ssl = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), SecureRandom()) }
        java.net.Socket().use { raw ->
            raw.connect(java.net.InetSocketAddress(host, port), timeoutMs)
            raw.soTimeout = timeoutMs
            (ssl.socketFactory.createSocket(raw, host, port, false) as javax.net.ssl.SSLSocket).use { socket ->
                socket.startHandshake()
                // 실제 동기화 요청(HttpsURLConnection)이 하는 SAN IP 검증과 같은 조건을 먼저 확인
                certificateCoversIpAddress(peer.get() ?: return@use false, host)
            }
        }
    }.getOrDefault(false)
}

/** IP 리터럴 접속용 SAN(iPAddress) 일치 검사. 이름(DNS)·와일드카드는 인정하지 않는다. */
fun certificateCoversIpAddress(certificate: X509Certificate, host: String): Boolean {
    if (!host.matches(Regex("[0-9]{1,3}(\\.[0-9]{1,3}){3}"))) return false // IP 리터럴만(DNS 조회 금지)
    val wanted = runCatching { java.net.InetAddress.getByName(host).hostAddress }.getOrNull() ?: return false
    val names = certificate.subjectAlternativeNames ?: return false
    return names.any { entry ->
        entry.size >= 2 && entry[0] == 7 &&
            runCatching { java.net.InetAddress.getByName(entry[1] as String).hostAddress == wanted }.getOrDefault(false)
    }
}

fun matchesPinnedServerIdentity(
    actualLeafSha256: String,
    actualSpkiSha256: String,
    expectedLeafSha256: String,
    expectedSpkiSha256: String?,
): Boolean {
    val leafMatches = MessageDigest.isEqual(
        actualLeafSha256.lowercase().toByteArray(),
        expectedLeafSha256.lowercase().replace(":", "").toByteArray(),
    )
    val identityMatches = expectedSpkiSha256?.let {
        MessageDigest.isEqual(
            actualSpkiSha256.lowercase().toByteArray(),
            it.lowercase().replace(":", "").toByteArray(),
        )
    } ?: false
    return leafMatches || identityMatches
}

private fun sha256Hex(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
    .digest(bytes).joinToString("") { "%02x".format(it) }

fun validatePinnedServerCertificate(
    certificate: X509Certificate,
    expectedLeafSha256: String,
    expectedSpkiSha256: String?,
): Pair<String, String> {
    try {
        certificate.checkValidity()
        certificate.verify(certificate.publicKey)
    } catch (error: Exception) {
        throw CertificateException("Invalid self-signed server certificate", error)
    }
    val usages = certificate.extendedKeyUsage
    if (usages == null || "1.3.6.1.5.5.7.3.1" !in usages) {
        throw CertificateException("Certificate is not valid for TLS server authentication")
    }
    val leafSha256 = sha256Hex(certificate.encoded)
    val spkiSha256 = sha256Hex(certificate.publicKey.encoded)
    if (!matchesPinnedServerIdentity(leafSha256, spkiSha256, expectedLeafSha256, expectedSpkiSha256)) {
        throw CertificateException("Server certificate identity mismatch")
    }
    return leafSha256 to spkiSha256
}

fun validatePeerTlsIdentity(
    serverReportedSpkiSha256: String,
    peerSpkiSha256: String,
    storedSpkiSha256: String?,
): String {
    val reported = serverReportedSpkiSha256.lowercase().replace(":", "")
    val peer = peerSpkiSha256.lowercase().replace(":", "")
    val stored = storedSpkiSha256?.lowercase()?.replace(":", "")
    if (!reported.matches(Regex("[0-9a-f]{64}")) || !peer.matches(Regex("[0-9a-f]{64}"))) {
        throw SecurityException("Invalid TLS identity proof")
    }
    if (!MessageDigest.isEqual(reported.toByteArray(), peer.toByteArray())) {
        throw SecurityException("TLS identity response does not match peer certificate")
    }
    if (stored != null && !MessageDigest.isEqual(stored.toByteArray(), peer.toByteArray())) {
        throw SecurityException("Registered TLS identity changed")
    }
    return peer
}

data class LanSyncResponse(
    val body: JSONObject,
    val peerCertificateSha256: String,
    val peerSpkiSha256: String,
)

/** HTTPS-only client that trusts exactly one user-confirmed leaf certificate pin. */
class LanSyncClient {
    suspend fun post(connection: SyncConnection, path: String, body: JSONObject, authenticated: Boolean = true): JSONObject =
        postWithPeerIdentity(connection, path, body, authenticated).body

    suspend fun postWithPeerIdentity(
        connection: SyncConnection,
        path: String,
        body: JSONObject,
        authenticated: Boolean = true,
    ): LanSyncResponse =
        withContext(Dispatchers.IO) {
            require(connection.endpoint.startsWith("https://")) { "HTTPS endpoint required" }
            val pin = connection.certificateSha256.lowercase().replace(":", "")
            require(pin.matches(Regex("[0-9a-f]{64}"))) { "Invalid certificate pin" }
            val identityPin = connection.tlsIdentitySpkiSha256?.lowercase()?.replace(":", "")
            require(identityPin == null || identityPin.matches(Regex("[0-9a-f]{64}"))) {
                "Invalid TLS identity pin"
            }
            val peerLeaf = AtomicReference<String>()
            val peerSpki = AtomicReference<String>()
            val trust = object : X509TrustManager {
                override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
                override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) = Unit
                override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                    val leaf = chain.firstOrNull() ?: throw java.security.cert.CertificateException("Missing server certificate")
                    val (leafSha256, spkiSha256) = validatePinnedServerCertificate(leaf, pin, identityPin)
                    peerLeaf.set(leafSha256)
                    peerSpki.set(spkiSha256)
                }
            }
            val ssl = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), SecureRandom()) }
            val connectionUrl = URL(connection.endpoint + path)
            val http = (connectionUrl.openConnection() as HttpsURLConnection).apply {
                sslSocketFactory = ssl.socketFactory
                requestMethod = "POST"
                connectTimeout = 8_000
                readTimeout = 20_000
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
                if (authenticated) setRequestProperty("Authorization", "Bearer ${connection.token}")
            }
            http.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val code = http.responseCode
            val text = (if (code in 200..299) http.inputStream else http.errorStream)
                ?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (code !in 200..299) throw LanSyncHttpException(code)
            LanSyncResponse(
                body = JSONObject(text),
                peerCertificateSha256 = peerLeaf.get()
                    ?: throw CertificateException("Missing validated peer certificate"),
                peerSpkiSha256 = peerSpki.get()
                    ?: throw CertificateException("Missing validated peer TLS identity"),
            )
        }
}
