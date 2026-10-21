package com.insuranceai.android.sync

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.URL
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.X509Certificate
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/** HTTPS-only client that trusts exactly one user-confirmed leaf certificate pin. */
class LanSyncClient {
    suspend fun post(connection: SyncConnection, path: String, body: JSONObject, authenticated: Boolean = true): JSONObject =
        withContext(Dispatchers.IO) {
            require(connection.endpoint.startsWith("https://")) { "HTTPS endpoint required" }
            val pin = connection.certificateSha256.lowercase().replace(":", "")
            require(pin.matches(Regex("[0-9a-f]{64}"))) { "Invalid certificate pin" }
            val trust = object : X509TrustManager {
                override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
                override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) = Unit
                override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                    val leaf = chain.firstOrNull() ?: throw java.security.cert.CertificateException("Missing server certificate")
                    val actual = MessageDigest.getInstance("SHA-256").digest(leaf.encoded)
                        .joinToString("") { "%02x".format(it) }
                    if (!MessageDigest.isEqual(actual.toByteArray(), pin.toByteArray())) {
                        throw java.security.cert.CertificateException("Server certificate pin mismatch")
                    }
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
            if (code !in 200..299) throw IllegalStateException("LAN sync HTTP $code")
            JSONObject(text)
        }
}
