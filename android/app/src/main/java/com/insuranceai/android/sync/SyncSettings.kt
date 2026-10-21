package com.insuranceai.android.sync

import android.content.Context
import com.insuranceai.android.security.EncryptionService

data class SyncConnection(val endpoint: String, val certificateSha256: String, val token: String)

class SyncSettings(context: Context, private val encryption: EncryptionService) {
    private val prefs = context.applicationContext.getSharedPreferences("lan_sync", Context.MODE_PRIVATE)

    fun save(endpoint: String, certificateSha256: String, token: String) {
        require(endpoint.startsWith("https://")) { "HTTPS endpoint required" }
        val pin = certificateSha256.lowercase().replace(":", "")
        require(pin.matches(Regex("[0-9a-f]{64}"))) { "SHA-256 certificate pin required" }
        prefs.edit()
            .putString("endpoint", endpoint.trimEnd('/'))
            .putString("certificate_sha256", pin)
            .putString("token_encrypted", encryption.encrypt(token))
            .apply()
    }

    fun load(): SyncConnection? {
        val endpoint = prefs.getString("endpoint", null) ?: return null
        val pin = prefs.getString("certificate_sha256", null) ?: return null
        val encrypted = prefs.getString("token_encrypted", null) ?: return null
        return SyncConnection(endpoint, pin, encryption.decrypt(encrypted))
    }

    fun clear() = prefs.edit().clear().apply()
}
