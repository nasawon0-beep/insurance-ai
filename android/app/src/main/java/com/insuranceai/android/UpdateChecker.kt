package com.insuranceai.android

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.URL

/**
 * Remote update metadata returned by the Android update API.
 *
 * Usage:
 *   val update = UpdateChecker(context).checkForUpdate()
 *   if (update != null) show an update dialog and pass update.downloadUrl to DownloadManager.
 */
data class UpdateInfo(
    val version: String,
    val downloadUrl: String,
    val changelog: String,
    val required: Boolean
)

/** Checks the published Android update JSON and returns only newer versions. */
class UpdateChecker(private val context: Context) {
    private val updateUrl = "https://nasangwon.github.io/insurance-ai-updates/api/android.json"

    suspend fun checkForUpdate(): UpdateInfo? = withContext(Dispatchers.IO) {
        try {
            val response = URL(updateUrl).readText()
            val json = JSONObject(response)

            val latestVersion = json.getString("latest_version")
            val currentVersion = BuildConfig.VERSION_NAME

            if (compareVersions(latestVersion, currentVersion) > 0) {
                UpdateInfo(
                    version = latestVersion,
                    downloadUrl = json.getString("download_url"),
                    changelog = json.optString("changelog", ""),
                    required = json.optBoolean("required", false)
                )
            } else {
                null
            }
        } catch (e: Exception) {
            e.printStackTrace()
            null
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
