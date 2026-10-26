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
    val tlsIdentitySpkiSha256: String? = null,
)

enum class ConnectionRegistrationState { UNREGISTERED, ACTIVE, RECOVERY_REQUIRED }
enum class TlsBindingState { NOT_REQUIRED, PENDING, READY, FAILED }

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
        tlsIdentitySpkiSha256: String? = null,
    ) {
        require(endpoint.startsWith("https://")) { "HTTPS endpoint required" }
        val normalizedEndpoint = endpoint.trimEnd('/')
        val pin = certificateSha256.lowercase().replace(":", "")
        require(pin.matches(Regex("[0-9a-f]{64}"))) { "SHA-256 certificate pin required" }
        require(token.isNotBlank()) { "Pairing token required" }
        val identityPin = tlsIdentitySpkiSha256?.lowercase()?.replace(":", "")
        require(identityPin == null || identityPin.matches(Regex("[0-9a-f]{64}"))) {
            "SHA-256 TLS identity pin required"
        }

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
            identityPin?.let { values["tls_identity_spki_sha256"] = it }
            if (identityPin != null) values["tls_binding_state"] = TlsBindingState.PENDING.name
            val committed = prefs.putStrings(values)
            check(committed) { "credential preferences commit failed" }
            val loaded = load()
            check(
                loaded?.endpoint == normalizedEndpoint &&
                    loaded.certificateSha256 == pin &&
                    loaded.token == token &&
                    loaded.deviceId == deviceId &&
                    loaded.pcInstanceId == pcInstanceId &&
                    loaded.tlsIdentitySpkiSha256 == identityPin
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
            prefs.getString("tls_identity_spki_sha256"),
        )
    }

    fun bindTlsIdentity(spkiSha256: String) {
        val pin = spkiSha256.lowercase().replace(":", "")
        require(pin.matches(Regex("[0-9a-f]{64}"))) { "SHA-256 TLS identity pin required" }
        check(prefs.putStrings(mapOf(
            "tls_identity_spki_sha256" to pin,
            "tls_binding_state" to TlsBindingState.READY.name,
        ))) {
            "TLS identity preferences commit failed"
        }
        check(load()?.tlsIdentitySpkiSha256 == pin) { "TLS identity preferences read-back failed" }
    }

    fun registeredPcInstanceId(): String? = prefs.getString("pc_instance_id")

    fun tlsBindingState(): TlsBindingState = when (prefs.getString("tls_binding_state")) {
        TlsBindingState.PENDING.name -> TlsBindingState.PENDING
        TlsBindingState.READY.name -> TlsBindingState.READY
        TlsBindingState.FAILED.name -> TlsBindingState.FAILED
        else -> if (prefs.getString("tls_identity_spki_sha256").isNullOrBlank()) {
            TlsBindingState.NOT_REQUIRED
        } else TlsBindingState.PENDING
    }

    fun markTlsBindingState(state: TlsBindingState) {
        check(prefs.putStrings(mapOf("tls_binding_state" to state.name))) {
            "TLS binding state commit failed"
        }
    }

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

    fun stageRecoveryToken(token: String) = stageRecovery(token)

    fun stageRecovery(token: String, endpoint: String? = null, certificateSha256: String? = null) {
        require(token.length >= 32) { "Recovery credential required" }
        require((endpoint == null) == (certificateSha256 == null)) { "Recovery endpoint and certificate pin must be staged together" }
        try {
            val encrypted = encryption.encrypt(token)
            val values = mutableMapOf("recovery_token_encrypted" to encrypted)
            if (endpoint != null && certificateSha256 != null) {
                val normalizedEndpoint = endpoint.trimEnd('/')
                val pin = certificateSha256.lowercase().replace(":", "")
                require(normalizedEndpoint.startsWith("https://")) { "HTTPS endpoint required" }
                require(pin.matches(Regex("[0-9a-f]{64}"))) { "SHA-256 certificate pin required" }
                values["recovery_endpoint"] = normalizedEndpoint
                values["recovery_certificate_sha256"] = pin
            }
            check(prefs.putStrings(values)) {
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

    fun stagedRecoveryConnection(current: SyncConnection): SyncConnection = current.copy(
        endpoint = prefs.getString("recovery_endpoint")?.takeIf(String::isNotBlank) ?: current.endpoint,
        certificateSha256 = prefs.getString("recovery_certificate_sha256")?.takeIf(String::isNotBlank)
            ?: current.certificateSha256,
    )

    fun promoteStagedRecovery(
        expectedDeviceId: String,
        expectedPcInstanceId: String,
        endpoint: String? = null,
        certificateSha256: String? = null,
        tlsIdentitySpkiSha256: String? = null,
    ) {
        val current = load() ?: throw PairingLocalSaveException(IllegalStateException("existing credential unavailable"))
        if (current.deviceId != expectedDeviceId || current.pcInstanceId != expectedPcInstanceId) {
            throw PairingLocalSaveException(IllegalStateException("recovery identity mismatch"))
        }
        val staged = prefs.getString("recovery_token_encrypted")
            ?.takeIf(String::isNotBlank)
            ?: throw PairingLocalSaveException(IllegalStateException("staged recovery unavailable"))
        val promotedEndpoint = endpoint?.trimEnd('/') ?: current.endpoint
        val promotedPin = certificateSha256?.lowercase()?.replace(":", "") ?: current.certificateSha256
        val promotedIdentity = tlsIdentitySpkiSha256?.lowercase()?.replace(":", "")
            ?: current.tlsIdentitySpkiSha256
        require(promotedEndpoint.startsWith("https://")) { "HTTPS endpoint required" }
        require(promotedPin.matches(Regex("[0-9a-f]{64}"))) { "SHA-256 certificate pin required" }
        require(promotedIdentity == null || promotedIdentity.matches(Regex("[0-9a-f]{64}"))) {
            "SHA-256 TLS identity pin required"
        }
        try {
            val values = mutableMapOf(
                "endpoint" to promotedEndpoint,
                "certificate_sha256" to promotedPin,
                "token_encrypted" to staged,
                "recovery_token_encrypted" to "",
                "recovery_endpoint" to "",
                "recovery_certificate_sha256" to "",
                "registration_state" to ConnectionRegistrationState.ACTIVE.name,
            )
            promotedIdentity?.let { values["tls_identity_spki_sha256"] = it }
            check(prefs.putStrings(values)) { "recovery promotion commit failed" }
            val loaded = load()
            check(
                loaded?.deviceId == expectedDeviceId &&
                    loaded.pcInstanceId == expectedPcInstanceId &&
                    loaded.token == encryption.decrypt(staged) &&
                    loaded.endpoint == promotedEndpoint &&
                    loaded.certificateSha256 == promotedPin &&
                    loaded.tlsIdentitySpkiSha256 == promotedIdentity
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
        tlsIdentitySpkiSha256: String? = prefs.getString("tls_identity_spki_sha256"),
    ) {
        require(endpoint.startsWith("https://")) { "HTTPS endpoint required" }
        val normalizedEndpoint = endpoint.trimEnd('/')
        val pin = certificateSha256.lowercase().replace(":", "")
        require(pin.matches(Regex("[0-9a-f]{64}"))) { "SHA-256 certificate pin required" }
        require(deviceId.isNotBlank()) { "Verified device identity required" }
        val identityPin = tlsIdentitySpkiSha256?.lowercase()?.replace(":", "")
        require(identityPin == null || identityPin.matches(Regex("[0-9a-f]{64}"))) {
            "SHA-256 TLS identity pin required"
        }
        val encrypted = prefs.getString("token_encrypted") ?: error("Pairing token unavailable")
        val values = mutableMapOf(
            "endpoint" to normalizedEndpoint,
            "certificate_sha256" to pin,
            "token_encrypted" to encrypted,
            "device_id" to deviceId,
        )
        pcInstanceId?.takeIf(String::isNotBlank)?.let { values["pc_instance_id"] = it }
        identityPin?.let { values["tls_identity_spki_sha256"] = it }
        check(prefs.putStrings(values)) { "verified address preferences commit failed" }
    }

    fun clear() {
        check(prefs.clear()) { "credential preferences clear failed" }
    }
}