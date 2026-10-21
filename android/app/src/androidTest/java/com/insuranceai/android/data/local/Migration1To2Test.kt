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
            execSQL("INSERT INTO customers (id,name,phone,birthDate,gender,email,addressEncrypted,occupation,tagsEncrypted,memoEncrypted,rrnEncrypted,rrnHash,customerStatus,importBatchId,createdAt,updatedAt,syncStatus) VALUES ('synthetic','합성 고객','010-0000-0000','1990-01-01','X','synthetic@example.invalid','enc-address','tester','enc-tags','enc-memo','enc-rrn','hash','ACTIVE','batch','before','before',1)")
            execSQL("INSERT INTO policies (id,customerId,insurer,productName,policyNumber,planType,premium,paymentCycle,startDate,endDate,status,memoEncrypted,documentId,insuredPeriodEncrypted,paymentPeriodEncrypted,isOwn,createdAt,updatedAt,syncStatus) VALUES ('policy','synthetic','합성보험','합성상품','SYNTHETIC-ONLY','test',12345,'MONTHLY','2026-01-01','2036-01-01','ACTIVE','enc-policy-memo','doc','enc-insured','enc-payment',1,'before','before',0)")
            execSQL("INSERT INTO consultations (id,customerId,consultedAt,channel,title,contentEncrypted,transcriptEncrypted,followUpAt,followUpDoneAt,createdAt,updatedAt,syncStatus) VALUES ('consultation','synthetic','2026-10-21T00:00:00Z','test','합성 상담','enc-content','enc-transcript','2026-10-22',NULL,'before','before',1)")
            close()
        }
        helper.runMigrationsAndValidate("migration-test", 2, true, *DatabaseMigrations.ALL).apply {
            query("SELECT name,phone,addressEncrypted,memoEncrypted,rrnEncrypted,syncStatus FROM customers WHERE id='synthetic'").use { cursor ->
                check(cursor.moveToFirst())
                check(cursor.getString(0) == "합성 고객")
                check(cursor.getString(1) == "010-0000-0000")
                check(cursor.getString(2) == "enc-address")
                check(cursor.getString(3) == "enc-memo")
                check(cursor.getString(4) == "enc-rrn")
                check(cursor.getInt(5) == 1)
            }
            query("SELECT insurer,productName,premium,memoEncrypted FROM policies WHERE id='policy'").use { cursor ->
                check(cursor.moveToFirst())
                check(cursor.getString(0) == "합성보험")
                check(cursor.getString(1) == "합성상품")
                check(cursor.getInt(2) == 12345)
                check(cursor.getString(3) == "enc-policy-memo")
            }
            query("SELECT customerId,title,contentEncrypted,transcriptEncrypted FROM consultations WHERE id='consultation'").use { cursor ->
                check(cursor.moveToFirst())
                check(cursor.getString(0) == "synthetic")
                check(cursor.getString(1) == "합성 상담")
                check(cursor.getString(2) == "enc-content")
                check(cursor.getString(3) == "enc-transcript")
            }
            for (table in listOf("coverage_analysis_results", "sync_outbox", "sync_conflicts", "sync_state", "sync_tombstone_acks")) {
                query("SELECT name FROM sqlite_master WHERE type='table' AND name='$table'").use { cursor ->
                    check(cursor.moveToFirst())
                }
            }
            close()
        }
    }
}
