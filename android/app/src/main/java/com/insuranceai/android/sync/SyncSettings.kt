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

enum class ConnectionRegistrationState { UNREGISTERED, ACTIVE, RECOVERY_REQUIRED }

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
                    "registration_state" to ConnectionRegistrationState.ACTIVE.name,
                    "recovery_token_encrypted" to "",
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

    fun registrationState(): ConnectionRegistrationState {
        if (!isConfigured()) return ConnectionRegistrationState.UNREGISTERED
        return when (prefs.getString("registration_state")) {
            ConnectionRegistrationState.RECOVERY_REQUIRED.name -> ConnectionRegistrationState.RECOVERY_REQUIRED
            else -> ConnectionRegistrationState.ACTIVE
        }
    }

    fun isReadyForAutomaticSync(): Boolean = registrationState() == ConnectionRegistrationState.ACTIVE

    fun markRecoveryRequired() {
        check(prefs.putStrings(mapOf("registration_state" to ConnectionRegistrationState.RECOVERY_REQUIRED.name))) {
            "registration state commit failed"
        }
    }

    fun markActive() {
        check(prefs.putStrings(mapOf("registration_state" to ConnectionRegistrationState.ACTIVE.name))) {
            "registration state commit failed"
        }
    }

    fun stageRecoveryToken(token: String) {
        require(token.length >= 32) { "Recovery credential required" }
        try {
            val encrypted = encryption.encrypt(token)
            check(prefs.putStrings(mapOf("recovery_token_encrypted" to encrypted))) {
                "recovery staging commit failed"
            }
        } catch (error: PairingLocalSaveException) {
            throw error
        } catch (error: Throwable) {
            throw PairingLocalSaveException(error)
        }
    }

    fun stagedRecoveryToken(): String? = prefs.getString("recovery_token_encrypted")
        ?.takeIf(String::isNotBlank)
        ?.let(encryption::decrypt)

    fun hasStagedRecovery(): Boolean = !prefs.getString("recovery_token_encrypted").isNullOrBlank()

    fun promoteStagedRecovery(expectedDeviceId: String, expectedPcInstanceId: String) {
        val current = load() ?: throw PairingLocalSaveException(IllegalStateException("existing credential unavailable"))
        if (current.deviceId != expectedDeviceId || current.pcInstanceId != expectedPcInstanceId) {
            throw PairingLocalSaveException(IllegalStateException("recovery identity mismatch"))
        }
        val staged = prefs.getString("recovery_token_encrypted")
            ?.takeIf(String::isNotBlank)
            ?: throw PairingLocalSaveException(IllegalStateException("staged recovery unavailable"))
        try {
            check(prefs.putStrings(mapOf(
                "token_encrypted" to staged,
                "recovery_token_encrypted" to "",
                "registration_state" to ConnectionRegistrationState.ACTIVE.name,
            ))) { "recovery promotion commit failed" }
            val loaded = load()
            check(
                loaded?.deviceId == expectedDeviceId &&
                    loaded.pcInstanceId == expectedPcInstanceId &&
                    loaded.token == encryption.decrypt(staged)
            ) { "recovery promotion read-back failed" }
        } catch (error: PairingLocalSaveException) {
            throw error
        } catch (error: Throwable) {
            throw PairingLocalSaveException(error)
        }
    }

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