package com.insuranceai.android.security

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class EncryptionServiceTest {
    private val service = EncryptionService(
        FixedCryptoKeyProvider(
            dbPassphrase = ByteArray(32) { (it + 1).toByte() },
            fieldAesKey = ByteArray(32) { (it + 33).toByte() },
            hmacKey = ByteArray(32) { (it + 65).toByte() },
        )
    )

    @Test
    fun encryptDecryptRoundTripProtectsPlaintext() {
        val plaintext = "900101-1234567 서울시 고객 메모"

        val encrypted = service.encrypt(plaintext)

        assertTrue(encrypted.startsWith("enc:v1:"))
        assertNotEquals(plaintext, encrypted)
        assertEquals(plaintext, service.decrypt(encrypted))
    }

    @Test
    fun rrnHashIsStableAndDoesNotContainRrn() {
        val rrn = "900101-1234567"

        val first = service.hmacSha256(rrn)
        val second = service.hmacSha256(rrn)

        assertEquals(first, second)
        assertTrue(first.length >= 40)
        assertTrue(!first.contains(rrn))
    }

    @Test
    fun fixedProviderSeparatesDatabaseFieldEncryptionAndHmacKeys() {
        val provider = FixedCryptoKeyProvider(
            dbPassphrase = ByteArray(32) { 1 },
            fieldAesKey = ByteArray(32) { 2 },
            hmacKey = ByteArray(32) { 3 },
        )

        assertFalse(provider.getOrCreateDbPassphrase().contentEquals(provider.getOrCreateFieldAesKey()))
        assertFalse(provider.getOrCreateDbPassphrase().contentEquals(provider.getOrCreateHmacKey()))
        assertFalse(provider.getOrCreateFieldAesKey().contentEquals(provider.getOrCreateHmacKey()))
    }

    @Test
    fun keyStoreMasterKeyRequiresUserAuthentication() {
        val source = File("src/main/java/com/insuranceai/android/security/Encryption.kt").readText()

        assertTrue(source.contains(".setUserAuthenticationRequired(true)"))
        assertTrue(source.contains(".setUserAuthenticationValidityDurationSeconds(300)"))
    }
}
