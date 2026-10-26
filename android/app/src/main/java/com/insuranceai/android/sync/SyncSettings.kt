package com.insuranceai.android.sync

import android.content.Context
import android.content.SharedPreferences
import com.insuranceai.android.security.EncryptionService

data class SyncConnection(
    val endpoint: String,
    val certificateSha256: String,
    val token: String,
    val deviceId: String? = null,
    val pcInstanceId: String? = null,
)

interface SyncTokenCipher {
    fun verifyAvailable()
    fun encrypt(plaintext: String): String
    fun decrypt(ciphertext: String): String
}

interface SyncPreferenceStore {
    fun getString(key: String): String?
    fun putStrings(values: Map<String, String>): Boolean
    fun clear(): Boolean
}

private class SharedPreferencesStore(private val prefs: SharedPreferences) : SyncPreferenceStore {
    override fun getString(key: String): String? = prefs.getString(key, null)

    override fun putStrings(values: Map<String, String>): Boolean {
        val editor = prefs.edit()
        values.forEach(editor::putString)
        return editor.commit()
    }

    override fun clear(): Boolean = prefs.edit().clear().commit()
}

class SyncSettings(
    private val prefs: SyncPreferenceStore,
    private val encryption: SyncTokenCipher,
) {
    constructor(context: Context, encryption: EncryptionService) : this(
        SharedPreferencesStore(
            context.applicationContext.getSharedPreferences("lan_sync", Context.MODE_PRIVATE)
        ),
        encryption,
    )

    fun verifyCredentialStorageAvailable() {
        encryption.verifyAvailable()
    }

    fun isConfigured(): Boolean =
        !prefs.getString("endpoint").isNullOrBlank() &&
            !prefs.getString("certificate_sha256").isNullOrBlank() &&
            !prefs.getString("token_encrypted").isNullOrBlank()

    fun endpointLabel(): String? = prefs.getString("endpoint")

    fun save(
        endpoint: String,
        certificateSha256: String,
        token: String,
        deviceId: String? = null,
        pcInstanceId: String? = null,
    ) {
        require(endpoint.startsWith("https://")) { "HTTPS endpoint required" }
        val normalizedEndpoint = endpoint.trimEnd('/')
        val pin = certificateSha256.lowercase().replace(":", "")
        require(pin.matches(Regex("[0-9a-f]{64}"))) { "SHA-256 certificate pin required" }
        require(token.isNotBlank()) { "Pairing token required" }

        try {
            val encryptedToken = encryption.encrypt(token)
            val values = mutableMapOf(
                    "endpoint" to normalizedEndpoint,
                    "certificate_sha256" to pin,
                    "token_encrypted" to encryptedToken,
                )
            deviceId?.takeIf(String::isNotBlank)?.let { values["device_id"] = it }
            pcInstanceId?.takeIf(String::isNotBlank)?.let { values["pc_instance_id"] = it }
            val committed = prefs.putStrings(values)
            check(committed) { "credential preferences commit failed" }
            val loaded = load()
            check(
                loaded?.endpoint == normalizedEndpoint &&
                    loaded.certificateSha256 == pin &&
                    loaded.token == token &&
                    loaded.deviceId == deviceId &&
                    loaded.pcInstanceId == pcInstanceId
            ) { "credential preferences read-back failed" }
        } catch (error: PairingLocalSaveException) {
            throw error
        } catch (error: Throwable) {
            throw PairingLocalSaveException(error)
        }
    }

    fun load(): SyncConnection? {
        val endpoint = prefs.getString("endpoint") ?: return null
        val pin = prefs.getString("certificate_sha256") ?: return null
        val encrypted = prefs.getString("token_encrypted") ?: return null
        return SyncConnection(
            endpoint,
            pin,
            encryption.decrypt(encrypted),
            prefs.getString("device_id"),
            prefs.getString("pc_instance_id"),
        )
    }

    fun registeredPcInstanceId(): String? = prefs.getString("pc_instance_id")

    fun updateVerifiedAddress(
        endpoint: String,
        certificateSha256: String,
        deviceId: String,
        pcInstanceId: String? = prefs.getString("pc_instance_id"),
    ) {
        require(endpoint.startsWith("https://")) { "HTTPS endpoint required" }
        val normalizedEndpoint = endpoint.trimEnd('/')
        val pin = certificateSha256.lowercase().replace(":", "")
        require(pin.matches(Regex("[0-9a-f]{64}"))) { "SHA-256 certificate pin required" }
        require(deviceId.isNotBlank()) { "Verified device identity required" }
        val encrypted = prefs.getString("token_encrypted") ?: error("Pairing token unavailable")
        val values = mutableMapOf(
            "endpoint" to normalizedEndpoint,
            "certificate_sha256" to pin,
            "token_encrypted" to encrypted,
            "device_id" to deviceId,
        )
        pcInstanceId?.takeIf(String::isNotBlank)?.let { values["pc_instance_id"] = it }
        check(prefs.putStrings(values)) { "verified address preferences commit failed" }
    }

    fun clear() {
        check(prefs.clear()) { "credential preferences clear failed" }
    }
}