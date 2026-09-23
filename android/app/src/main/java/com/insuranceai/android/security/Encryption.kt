package com.insuranceai.android.security

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.Mac
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import java.util.Base64

interface CryptoKeyProvider {
    fun getOrCreateDbPassphrase(): ByteArray
    fun getOrCreateFieldAesKey(): ByteArray
    fun getOrCreateHmacKey(): ByteArray
}

class FixedCryptoKeyProvider(
    private val dbPassphrase: ByteArray,
    private val fieldAesKey: ByteArray,
    private val hmacKey: ByteArray,
) : CryptoKeyProvider {
    init {
        require(dbPassphrase.size == 32) { "DB passphrase must be 32 bytes" }
        require(fieldAesKey.size == 32) { "AES-256 key must be 32 bytes" }
        require(hmacKey.size == 32) { "HMAC key must be 32 bytes" }
    }

    override fun getOrCreateDbPassphrase(): ByteArray = dbPassphrase.copyOf()
    override fun getOrCreateFieldAesKey(): ByteArray = fieldAesKey.copyOf()
    override fun getOrCreateHmacKey(): ByteArray = hmacKey.copyOf()
}

class KeyStoreManager(context: Context) : CryptoKeyProvider {
    private val appContext = context.applicationContext
    private val prefs: SharedPreferences = appContext.getSharedPreferences("secure", Context.MODE_PRIVATE)
    private val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    private val masterAlias = "insurance_ai_master_key"
    private val passphraseKey = "db_passphrase_enc"
    private val fieldAesKey = "field_aes_key_enc"
    private val hmacKey = "hmac_key_enc"

    override fun getOrCreateDbPassphrase(): ByteArray = getOrCreateWrappedKey(passphraseKey)

    override fun getOrCreateFieldAesKey(): ByteArray = getOrCreateWrappedKey(fieldAesKey)

    override fun getOrCreateHmacKey(): ByteArray = getOrCreateWrappedKey(hmacKey)

    private fun getOrCreateWrappedKey(prefKey: String): ByteArray {
        ensureMasterKey()
        prefs.getString(prefKey, null)?.let { return decryptWithMaster(it) }

        val key = ByteArray(32).also { SecureRandom().nextBytes(it) }
        prefs.edit().putString(prefKey, encryptWithMaster(key)).apply()
        return key
    }

    private fun ensureMasterKey() {
        if (keyStore.containsAlias(masterAlias)) return
        val keyGenerator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        keyGenerator.init(
            KeyGenParameterSpec.Builder(
                masterAlias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .setUserAuthenticationRequired(true)
                .setUserAuthenticationValidityDurationSeconds(300)
                .build()
        )
        keyGenerator.generateKey()
    }

    private fun masterKey(): SecretKey = keyStore.getKey(masterAlias, null) as SecretKey

    private fun encryptWithMaster(plaintext: ByteArray): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, masterKey())
        return Base64.getEncoder().encodeToString(cipher.iv + cipher.doFinal(plaintext))
    }

    private fun decryptWithMaster(encoded: String): ByteArray {
        val combined = Base64.getDecoder().decode(encoded)
        val iv = combined.copyOfRange(0, 12)
        val ciphertext = combined.copyOfRange(12, combined.size)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, masterKey(), GCMParameterSpec(128, iv))
        return cipher.doFinal(ciphertext)
    }
}

class EncryptionService(private val keyProvider: CryptoKeyProvider) {
    fun encrypt(plaintext: String): String {
        if (plaintext.isBlank()) return plaintext
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(keyProvider.getOrCreateFieldAesKey(), "AES"))
        return "enc:v1:" + Base64.getEncoder().encodeToString(cipher.iv + cipher.doFinal(plaintext.toByteArray(Charsets.UTF_8)))
    }

    fun decrypt(ciphertext: String): String {
        if (ciphertext.isBlank() || !ciphertext.startsWith("enc:v1:")) return ciphertext
        val combined = Base64.getDecoder().decode(ciphertext.removePrefix("enc:v1:"))
        val iv = combined.copyOfRange(0, 12)
        val encrypted = combined.copyOfRange(12, combined.size)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(keyProvider.getOrCreateFieldAesKey(), "AES"), GCMParameterSpec(128, iv))
        return String(cipher.doFinal(encrypted), Charsets.UTF_8)
    }

    fun hmacSha256(value: String): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(keyProvider.getOrCreateHmacKey(), "HmacSHA256"))
        return mac.doFinal(value.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
    }
}
