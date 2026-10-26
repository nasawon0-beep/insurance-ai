package com.insuranceai.android.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Assert.assertFalse
import org.junit.Test

class SyncSettingsTest {
    private val connection = SyncConnection(
        endpoint = "https://192.0.2.1:18443/",
        certificateSha256 = "AA:".repeat(31) + "AA",
        token = "fixture-token-not-a-real-secret",
    )

    @Test
    fun savedConnectionCanBeReadAfterSettingsRecreation() {
        val store = MemoryStore()
        val cipher = FixtureCipher()

        SyncSettings(store, cipher).save(
            connection.endpoint,
            connection.certificateSha256,
            connection.token,
        )
        val loadedAfterRestart = SyncSettings(store, cipher).load()

        assertEquals(connection.endpoint.trimEnd('/'), loadedAfterRestart?.endpoint)
        assertEquals(connection.certificateSha256.lowercase().replace(":", ""), loadedAfterRestart?.certificateSha256)
        assertEquals(connection.token, loadedAfterRestart?.token)
        assertTrue(store.values.getValue("token_encrypted") != connection.token)
    }

    @Test
    fun commitFailureIsReportedAsLocalSaveFailure() {
        val store = MemoryStore(commitResult = false)
        val error = assertThrows(PairingLocalSaveException::class.java) {
            SyncSettings(store, FixtureCipher()).save(
                connection.endpoint,
                connection.certificateSha256,
                connection.token,
            )
        }

        assertTrue(error.cause is IllegalStateException)
    }

    @Test
    fun prepareChecksCipherBeforeAnyNetworkAction() {
        val cipher = FixtureCipher(verifyFailure = IllegalStateException("User not authenticated"))
        val error = assertThrows(IllegalStateException::class.java) {
            SyncSettings(MemoryStore(), cipher).verifyCredentialStorageAvailable()
        }

        assertTrue(error.message.orEmpty().contains("User not authenticated"))
    }

    @Test
    fun verifiedAddressUpdatePreservesEncryptedTokenAndSurvivesRestart() {
        val store = MemoryStore()
        val cipher = FixtureCipher()
        val settings = SyncSettings(store, cipher)
        settings.save(connection.endpoint, connection.certificateSha256, connection.token)
        val encryptedBefore = store.values.getValue("token_encrypted")

        val instance = "0123456789abcdef0123456789abcdef"
        settings.updateVerifiedAddress(
            "https://192.0.2.2:55902",
            "b".repeat(64),
            "same-device",
            instance,
        )
        val restarted = SyncSettings(store, cipher).load()

        assertEquals("https://192.0.2.2:55902", restarted?.endpoint)
        assertEquals("b".repeat(64), restarted?.certificateSha256)
        assertEquals("same-device", restarted?.deviceId)
        assertEquals(instance, restarted?.pcInstanceId)
        assertEquals(connection.token, restarted?.token)
        assertEquals(encryptedBefore, store.values.getValue("token_encrypted"))
    }

    @Test
    fun tlsIdentityBindingAndAddressUpdateArePersistedAtomically() {
        val store = MemoryStore()
        val settings = SyncSettings(store, FixtureCipher())
        val identity = "c".repeat(64)
        settings.save(
            connection.endpoint,
            connection.certificateSha256,
            connection.token,
            "same-device",
            "a".repeat(32),
        )
        assertEquals(TlsBindingState.NOT_REQUIRED, settings.tlsBindingState())

        settings.bindTlsIdentity(identity)
        assertEquals(identity, SyncSettings(store, FixtureCipher()).load()?.tlsIdentitySpkiSha256)
        assertEquals(TlsBindingState.READY, settings.tlsBindingState())
        settings.markTlsBindingState(TlsBindingState.FAILED)
        assertEquals(TlsBindingState.FAILED, settings.tlsBindingState())

        settings.updateVerifiedAddress(
            endpoint = "https://192.0.2.2:18443",
            certificateSha256 = "b".repeat(64),
            deviceId = "same-device",
            pcInstanceId = "a".repeat(32),
            tlsIdentitySpkiSha256 = identity,
        )
        val restarted = SyncSettings(store, FixtureCipher()).load()
        assertEquals("https://192.0.2.2:18443", restarted?.endpoint)
        assertEquals("b".repeat(64), restarted?.certificateSha256)
        assertEquals(identity, restarted?.tlsIdentitySpkiSha256)
    }

    @Test
    fun failedVerifiedAddressCommitPreservesOldTuple() {
        val store = MemoryStore()
        val settings = SyncSettings(store, FixtureCipher())
        settings.save(connection.endpoint, connection.certificateSha256, connection.token)
        val before = store.values.toMap()
        store.commitResult = false

        assertThrows(IllegalStateException::class.java) {
            settings.updateVerifiedAddress("https://192.0.2.2:55902", "b".repeat(64), "same-device")
        }

        assertEquals(before, store.values)
    }

    @Test
    fun revokedStateAndStagedRecoveryPreserveExistingCredentialUntilAtomicPromotion() {
        val store = MemoryStore()
        val settings = SyncSettings(store, FixtureCipher())
        settings.save(connection.endpoint, connection.certificateSha256, connection.token, "same-device", "a".repeat(32))
        settings.markRecoveryRequired()
        settings.stageRecoveryToken("replacement-token-with-sufficient-entropy")

        assertEquals(ConnectionRegistrationState.RECOVERY_REQUIRED, settings.registrationState())
        assertFalse(settings.isReadyForAutomaticSync())
        assertTrue(settings.hasStagedRecovery())
        assertEquals(connection.token, settings.load()?.token)
        assertEquals("replacement-token-with-sufficient-entropy", settings.stagedRecoveryToken())

        settings.promoteStagedRecovery("same-device", "a".repeat(32))
        assertEquals("replacement-token-with-sufficient-entropy", settings.load()?.token)
        assertEquals(ConnectionRegistrationState.ACTIVE, settings.registrationState())
        assertEquals(null, settings.stagedRecoveryToken())
        assertFalse(settings.hasStagedRecovery())
    }

    @Test
    fun failedRecoveryPromotionLeavesOldCredentialAndStageRecoverable() {
        val store = MemoryStore()
        val settings = SyncSettings(store, FixtureCipher())
        settings.save(connection.endpoint, connection.certificateSha256, connection.token, "same-device", "b".repeat(32))
        settings.markRecoveryRequired()
        settings.stageRecoveryToken("replacement-token-with-sufficient-entropy")
        store.commitResult = false

        assertThrows(PairingLocalSaveException::class.java) {
            settings.promoteStagedRecovery("same-device", "b".repeat(32))
        }

        store.commitResult = true
        assertEquals(connection.token, settings.load()?.token)
        assertEquals("replacement-token-with-sufficient-entropy", settings.stagedRecoveryToken())
        assertFalse(settings.registrationState() == ConnectionRegistrationState.ACTIVE)
    }

    @Test
    fun movedLegacyRecoveryStagesAndPromotesEndpointLeafIdentityAndTokenAtomically() {
        val store = MemoryStore()
        val settings = SyncSettings(store, FixtureCipher())
        settings.save(connection.endpoint, connection.certificateSha256, connection.token, "same-device", "c".repeat(32))
        settings.markRecoveryRequired()
        val movedEndpoint = "https://192.0.2.9:18443"
        val movedLeaf = "d".repeat(64)
        val movedIdentity = "e".repeat(64)
        settings.stageRecovery("replacement-token-with-sufficient-entropy", movedEndpoint, movedLeaf)

        assertEquals(movedEndpoint, settings.stagedRecoveryConnection(settings.load()!!).endpoint)
        assertEquals(movedLeaf, settings.stagedRecoveryConnection(settings.load()!!).certificateSha256)

        settings.promoteStagedRecovery(
            "same-device",
            "c".repeat(32),
            movedEndpoint,
            movedLeaf,
            movedIdentity,
        )
        val promoted = settings.load()!!
        assertEquals(movedEndpoint, promoted.endpoint)
        assertEquals(movedLeaf, promoted.certificateSha256)
        assertEquals(movedIdentity, promoted.tlsIdentitySpkiSha256)
        assertEquals("replacement-token-with-sufficient-entropy", promoted.token)
        assertEquals(ConnectionRegistrationState.ACTIVE, settings.registrationState())
        assertFalse(settings.hasStagedRecovery())
    }

    private class MemoryStore(
        var commitResult: Boolean = true,
        val values: MutableMap<String, String> = mutableMapOf(),
    ) : SyncPreferenceStore {
        override fun getString(key: String): String? = values[key]

        override fun putStrings(values: Map<String, String>): Boolean {
            if (commitResult) this.values.putAll(values)
            return commitResult
        }

        override fun clear(): Boolean {
            values.clear()
            return true
        }
    }

    private class FixtureCipher(
        private val verifyFailure: Throwable? = null,
    ) : SyncTokenCipher {
        override fun verifyAvailable() {
            verifyFailure?.let { throw it }
        }

        override fun encrypt(plaintext: String): String = "encrypted:$plaintext"

        override fun decrypt(ciphertext: String): String = ciphertext.removePrefix("encrypted:")
    }
}
