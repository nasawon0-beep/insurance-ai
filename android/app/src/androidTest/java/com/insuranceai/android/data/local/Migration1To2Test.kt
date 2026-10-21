package com.insuranceai.android.data.local

import androidx.room.testing.MigrationTestHelper
import androidx.sqlite.db.framework.FrameworkSQLiteOpenHelperFactory
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class Migration1To2Test {
    @get:Rule
    val helper = MigrationTestHelper(
        InstrumentationRegistry.getInstrumentation(),
        AppDatabase::class.java,
        emptyList(),
        FrameworkSQLiteOpenHelperFactory()
    )

    @Test
    fun migrationPreservesExistingCustomerAndAddsSyncTables() {
        helper.createDatabase("migration-test", 1).apply {
            execSQL("INSERT INTO customers (id,name,createdAt,updatedAt,syncStatus) VALUES ('synthetic','합성 고객','before','before',0)")
            close()
        }
        helper.runMigrationsAndValidate("migration-test", 2, true, *DatabaseMigrations.ALL).apply {
            query("SELECT name FROM customers WHERE id='synthetic'").use { cursor ->
                check(cursor.moveToFirst())
                check(cursor.getString(0) == "합성 고객")
            }
            query("SELECT name FROM sqlite_master WHERE type='table' AND name='sync_outbox'").use { cursor ->
                check(cursor.moveToFirst())
            }
            close()
        }
    }
}
