package com.insuranceai.android.sync

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.insuranceai.android.data.local.AppDatabase
import com.insuranceai.android.security.AuthenticationGate
import com.insuranceai.android.security.FixedCryptoKeyProvider
import com.insuranceai.android.security.EncryptionService
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

private class E2ePreferenceStore : SyncPreferenceStore {
    private val values = mutableMapOf<String, String>()
    override fun getString(key: String): String? = values[key]
    override fun putStrings(values: Map<String, String>): Boolean {
        this.values.putAll(values)
        return true
    }
    override fun clear(): Boolean { values.clear(); return true }
}

private object E2eTokenCipher : SyncTokenCipher {
    override fun verifyAvailable() = Unit
    override fun encrypt(plaintext: String): String = "e2e:$plaintext"
    override fun decrypt(ciphertext: String): String = ciphertext.removePrefix("e2e:")
}

@RunWith(AndroidJUnit4::class)
class FamilySyncTlsE2eTest {
    @Test
    fun actualPcApiReceivesFamilyThenTombstoneRemovesOnlyEdge() = runBlocking {
        val args = InstrumentationRegistry.getArguments()
        val endpoint = args.getString("familyEndpoint").orEmpty()
        val pin = args.getString("familyPin").orEmpty()
        val code = args.getString("familyCode").orEmpty()
        assumeTrue("scratch TLS server arguments required", endpoint.isNotBlank() && pin.isNotBlank() && code.isNotBlank())

        val context = ApplicationProvider.getApplicationContext<Context>()
        val database = Room.inMemoryDatabaseBuilder(context, AppDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        try {
            val repository = SyncRepository(
                database = database,
                encryption = EncryptionService(
                    FixedCryptoKeyProvider(ByteArray(32) { 1 }, ByteArray(32) { 2 }, ByteArray(32) { 3 }),
                    AuthenticationGate(),
                ),
                settings = SyncSettings(E2ePreferenceStore(), E2eTokenCipher),
            )
            repository.pairAndSave(PairingRequest(endpoint, pin, code, "모아둠 가족 E2E"))
            assertEquals(true, repository.syncOnce())

            val customers = database.customerDao().observeAll().first()
            val edge = database.familyRelationshipDao().observeAll().first().single()
            assertEquals(2, customers.size)
            assertNotNull(customers.firstOrNull { it.id == edge.customerAId })
            assertNotNull(customers.firstOrNull { it.id == edge.customerBId })

            delay(800)
            assertEquals(true, repository.syncOnce())
            assertEquals(0, database.familyRelationshipDao().observeAll().first().size)
            assertEquals(2, database.customerDao().observeAll().first().size)
        } finally {
            database.close()
        }
    }
}
