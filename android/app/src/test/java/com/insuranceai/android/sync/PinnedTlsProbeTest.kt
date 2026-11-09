package com.insuranceai.android.sync

import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress
import java.security.KeyStore
import java.util.concurrent.atomic.AtomicInteger
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLSocket
import kotlin.concurrent.thread

/** 실제 TLS 소켓(루프백)으로 대체 탐색 프로브를 검증. 합성 fixture(src/test/resources/lan-probe) 사용. */
class PinnedTlsProbeTest {
    private val info = JSONObject(javaClass.getResource("/lan-probe/lan-probe.json")!!.readText())
    private val spki = info.getString("spki_sha256")
    private val okLeaf = info.getString("ok_leaf_sha256")

    private fun <T> withServer(fixture: String, block: (port: Int, appBytes: AtomicInteger) -> T): T {
        val keyStore = KeyStore.getInstance("PKCS12").apply {
            this@PinnedTlsProbeTest.javaClass.getResourceAsStream("/lan-probe/$fixture")!!.use { load(it, "test".toCharArray()) }
        }
        val kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).apply { init(keyStore, "test".toCharArray()) }
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
    fun trustedSpkiAndMatchingSanPassWithoutSendingAnyApplicationBytes() = withServer("lan-probe-ok.p12") { port, appBytes ->
        // 주소가 바뀌어 leaf가 달라진 상황: 옛 leaf 핀은 틀리고 바인딩된 SPKI만 맞음
        assertTrue(runBlocking { probePinnedTlsIdentity("127.0.0.1", port, "0".repeat(64), spki, 1_500) })
        Thread.sleep(200)
        assertEquals(0, appBytes.get()) // 토큰·HTTP 요청 없음
    }

    @Test
    fun unknownIdentityIsRejected() = withServer("lan-probe-ok.p12") { port, _ ->
        assertFalse(runBlocking { probePinnedTlsIdentity("127.0.0.1", port, "0".repeat(64), "1".repeat(64), 1_500) })
        assertFalse(runBlocking { probePinnedTlsIdentity("127.0.0.1", port, "0".repeat(64), null, 1_500) })
        assertTrue(runBlocking { probePinnedTlsIdentity("127.0.0.1", port, okLeaf, null, 1_500) }) // 같은 leaf 핀은 허용
    }

    @Test
    fun sameKeyButSanForAnotherAddressFailsHostnameVerification() = withServer("lan-probe-san.p12") { port, _ ->
        assertFalse(runBlocking { probePinnedTlsIdentity("127.0.0.1", port, "0".repeat(64), spki, 1_500) })
    }

    @Test
    fun closedPortIsQuickFalse() {
        val port = java.net.ServerSocket(0).use { it.localPort }
        assertFalse(runBlocking { probePinnedTlsIdentity("127.0.0.1", port, okLeaf, spki, 300) })
    }
}
