package com.insuranceai.android

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

import java.net.URL

/**
 * Remote update metadata returned by the Android update API.
 *
 * Usage:
 *   val update = UpdateChecker(context).checkForUpdate()
 *   if (update != null) show a consent dialog, then open the validated HTTPS download in a browser.
 *   Android requires the user to open the downloaded APK and approve installation.
 */
data class UpdateInfo(
    val version: String,
    val downloadUrl: String,
    val changelog: String,
    val required: Boolean
)

/** Checks the published Android update JSON and returns only newer versions. */
class UpdateChecker(private val context: Context) {
    suspend fun checkForUpdate(): UpdateInfo? = withContext(Dispatchers.IO) {
        val connection = URL(UpdateMetadataPolicy.ENDPOINT).openConnection() as java.net.HttpURLConnection
        try {
            connection.connectTimeout = 8000
            connection.readTimeout = 8000
            connection.instanceFollowRedirects = false
            connection.useCaches = false
            if (connection.responseCode != 200) return@withContext null
            val bytes = connection.inputStream.use { input ->
                val output = java.io.ByteArrayOutputStream()
                val buffer = ByteArray(4096)
                while (output.size() <= UpdateMetadataPolicy.MAX_BYTES) {
                    val count = input.read(buffer, 0, minOf(buffer.size, UpdateMetadataPolicy.MAX_BYTES + 1 - output.size()))
                    if (count < 0) break
                    output.write(buffer, 0, count)
                }
                output.toByteArray()
            }
            if (bytes.size > UpdateMetadataPolicy.MAX_BYTES) return@withContext null
            UpdateMetadataPolicy.parse(bytes.toString(Charsets.UTF_8), BuildConfig.VERSION_CODE)
        } catch (_: Exception) {
            // No raw network payload, token or exception is logged.
            null
        } finally {
            connection.disconnect()
        }
    }

    companion object {
        internal fun compareVersions(v1: String, v2: String): Int {
            val parts1 = v1.split(".").map { it.toIntOrNull() ?: 0 }
            val parts2 = v2.split(".").map { it.toIntOrNull() ?: 0 }

            for (i in 0 until maxOf(parts1.size, parts2.size)) {
                val p1 = parts1.getOrNull(i) ?: 0
                val p2 = parts2.getOrNull(i) ?: 0
                if (p1 > p2) return 1
                if (p1 < p2) return -1
            }
            return 0
        }
    }
}
