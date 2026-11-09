package com.insuranceai.android.sync

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.BeforeClass
import org.junit.Test
import java.io.File
import java.net.InetAddress
import java.nio.file.Files
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLSocket
import kotlin.concurrent.thread

/**
 * 실제 TLS 소켓(루프백)으로 대체 탐색 프로브를 검증.
 * 키는 테스트 실행 때 JDK keytool로 임시 폴더에 만든다(저장소에 키 파일 없음, 실서비스 키 아님).
 * 같은 EC 키로 SAN 127.0.0.1 인증서(ok)와 SAN 127.0.0.2 인증서(san)를 만든다.
 */
class PinnedTlsProbeTest {
    companion object {
        private const val PASSWORD = "test-only"
        private lateinit var okStore: File
        private lateinit var sanStore: File
        private lateinit var spki: String
        private lateinit var okLeaf: String

        private fun sha256(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

        private fun keytool(vararg args: String) {
            val tool = File(System.getProperty("java.home"), "bin/" + if (File.separatorChar == '\\') "keytool.exe" else "keytool")
            val process = ProcessBuilder(listOf(tool.path) + args).redirectErrorStream(true).start()
            val output = process.inputStream.bufferedReader().readText()
            check(process.waitFor(60, TimeUnit.SECONDS) && process.exitValue() == 0) { "keytool failed: $output" }
        }

        private fun load(file: File) = KeyStore.getInstance("PKCS12").apply { file.inputStream().use { load(it, PASSWORD.toCharArray()) } }

        @BeforeClass
        @JvmStatic
        fun createSyntheticKeys() {
            val dir = Files.createTempDirectory("lan-probe").toFile().apply { deleteOnExit() }
            okStore = File(dir, "ok.p12")
            keytool(
                "-genkeypair", "-alias", "probe", "-keyalg", "EC", "-groupname", "secp256r1", "-sigalg", "SHA256withECDSA",
                "-dname", "CN=MoaDoom Synthetic LAN Probe", "-validity", "30", "-ext", "SAN=ip:127.0.0.1", "-ext", "EKU=serverAuth",
                "-storetype", "PKCS12", "-keystore", okStore.path, "-storepass", PASSWORD, "-keypass", PASSWORD,
            )
            // 같은 키로 다른 주소 SAN 인증서: 복사 후 자체서명 재발급
            sanStore = File(dir, "san.p12")
            okStore.copyTo(sanStore)
            keytool(
                "-selfcert", "-alias", "probe", "-sigalg", "SHA256withECDSA", "-dname", "CN=MoaDoom Synthetic LAN Probe",
                "-validity", "30", "-ext", "SAN=ip:127.0.0.2", "-ext", "EKU=serverAuth",
                "-keystore", sanStore.path, "-storepass", PASSWORD, "-keypass", PASSWORD,
            )
            val ok = load(okStore).getCertificate("probe") as X509Certificate
            val san = load(sanStore).getCertificate("probe") as X509Certificate
            check(ok.publicKey.encoded.contentEquals(san.publicKey.encoded))
            spki = sha256(ok.publicKey.encoded)
            okLeaf = sha256(ok.encoded)
        }
    }

    private fun <T> withServer(store: File, block: (port: Int, appBytes: AtomicInteger) -> T): T {
        val kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).apply { init(load(store), PASSWORD.toCharArray()) }
        val ssl = SSLContext.getInstance("TLS").apply { init(kmf.keyManagers, null, null) }
        val server = ssl.serverSocketFactory.createServerSocket(0, 4, InetAddress.getByName("127.0.0.1")) as SSLServerSocket
        val appBytes = AtomicInteger(0)
        val worker = thread(isDaemon = true) {
            runCatching {
                while (!server.isClosed) {
                    (server.accept() as SSLSocket).use { socket ->
                        socket.soTimeout = 2_000
                        runCatching {
                            socket.startHandshake()
                            val buffer = ByteArray(1024)
                            while (true) {
                                val read = socket.inputStream.read(buffer)
                                if (read < 0) break
                                appBytes.addAndGet(read)
                            }
                        }
                    }
                }
            }
        }
        try {
            return block(server.localPort, appBytes)
        } finally {
            server.close()
            worker.join(3_000)
        }
    }

    @Test
    fun trustedSpkiAndMatchingSanPassWithoutSendingAnyApplicationBytes() = withServer(okStore) { port, appBytes ->
        // 주소가 바뀌어 leaf가 달라진 상황: 옛 leaf 핀은 틀리고 바인딩된 SPKI만 맞음
        assertTrue(runBlocking { probePinnedTlsIdentity("127.0.0.1", port, "0".repeat(64), spki, 1_500) })
        Thread.sleep(200)
        assertEquals(0, appBytes.get()) // 토큰·HTTP 요청 없음
    }

    @Test
    fun unknownIdentityIsRejected() = withServer(okStore) { port, _ ->
        assertFalse(runBlocking { probePinnedTlsIdentity("127.0.0.1", port, "0".repeat(64), "1".repeat(64), 1_500) })
        assertFalse(runBlocking { probePinnedTlsIdentity("127.0.0.1", port, "0".repeat(64), null, 1_500) })
        assertTrue(runBlocking { probePinnedTlsIdentity("127.0.0.1", port, okLeaf, null, 1_500) }) // 같은 leaf 핀은 허용
    }

    @Test
    fun sameKeyButSanForAnotherAddressFailsHostnameVerification() = withServer(sanStore) { port, _ ->
        assertFalse(runBlocking { probePinnedTlsIdentity("127.0.0.1", port, "0".repeat(64), spki, 1_500) })
    }

    @Test
    fun closedPortIsQuickFalse() {
        val port = java.net.ServerSocket(0).use { it.localPort }
        assertFalse(runBlocking { probePinnedTlsIdentity("127.0.0.1", port, okLeaf, spki, 300) })
    }
}
