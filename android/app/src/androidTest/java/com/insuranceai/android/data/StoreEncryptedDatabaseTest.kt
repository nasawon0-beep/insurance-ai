package com.insuranceai.android.data

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.insuranceai.android.data.local.AppDatabase
import com.insuranceai.android.data.local.CustomerEntity
import net.zetetic.database.sqlcipher.SupportOpenHelperFactory
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.util.UUID

@RunWith(AndroidJUnit4::class)
class StoreEncryptedDatabaseTest {
    @Test fun encryptedDatabasePersistsSyntheticCustomerAcrossReopen() = runBlocking {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val name = "store-test-" + UUID.randomUUID() + ".db"
        System.loadLibrary("sqlcipher")
        fun open(): AppDatabase = Room.databaseBuilder(context, AppDatabase::class.java, name)
            .openHelperFactory(SupportOpenHelperFactory(ByteArray(32) { (it+1).toByte() }))
            .build()
        try {
            val db = open()
            try { db.customerDao().insert(CustomerEntity(id="synthetic-store", name="가상고객")) }
            finally { db.close() }
            assertFalse(context.getDatabasePath(name).readBytes().take(16).toByteArray().toString(Charsets.UTF_8).startsWith("SQLite format 3"))
            val reopened = open()
            try { assertEquals("가상고객", reopened.customerDao().getById("synthetic-store")?.name) }
            finally { reopened.close() }
        } finally { context.deleteDatabase(name) }
    }
}
