package com.insuranceai.android

import org.json.JSONObject
import java.net.URI

/** Metadata is untrusted; only increasing integer versionCode can offer an update. */
internal object UpdateMetadataPolicy {
    const val ENDPOINT = "https://nasawon0-beep.github.io/insurance-ai-updates/api/android.json"
    const val MAX_BYTES = 65536

    fun allowedDownload(url: String): Boolean = runCatching {
        val uri = URI(url)
        uri.scheme == "https" && uri.host == "github.com" && uri.port == -1 &&
            uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null &&
            Regex("^/nasawon0-beep/insurance-ai/releases/download/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\\.apk$").matches(uri.rawPath ?: "") &&
            !uri.rawPath.contains("/untagged-")
    }.getOrDefault(false)

    fun parse(text: String, currentCode: Int): UpdateInfo? = runCatching {
        require(text.toByteArray(Charsets.UTF_8).size <= MAX_BYTES)
        val json = JSONObject(text)
        val raw = json.get("versionCode")
        require(raw is Int || raw is Long)
        val code = (raw as Number).toLong()
        require(code in 1..Int.MAX_VALUE.toLong())
        if (code <= currentCode) return null
        val version = json.getString("versionName")
        require(version.length in 1..80 && version.none { it.isISOControl() })
        val url = json.getString("downloadUrl")
        require(allowedDownload(url))
        val digest = json.getJSONObject("checksum").getString("sha256")
        require(Regex("^[a-fA-F0-9]{64}$").matches(digest))
        UpdateInfo(version, url, "다운로드 후 APK를 열어 Android 설치 화면에서 승인하세요. 기존 앱을 삭제하지 마세요. 같은 앱 서명이 아니면 Android가 업데이트를 거부합니다.", false)
    }.getOrNull()
}
