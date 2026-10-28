package com.insuranceai.android.data.local

import androidx.room.testing.MigrationTestHelper
import androidx.sqlite.db.framework.FrameworkSQLiteOpenHelperFactory
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class Migration3To4Test {
    @get:Rule
    val helper = MigrationTestHelper(
        InstrumentationRegistry.getInstrumentation(),
        AppDatabase::class.java,
        emptyList(),
        FrameworkSQLiteOpenHelperFactory(),
    )

    @Test
    fun migrationAddsFamilyWithoutDeletingOfflineOrSyncState() {
        helper.createDatabase("migration-3-4-test", 3).apply {
            execSQL("INSERT INTO customers (id,name,phone,birthDate,gender,email,addressEncrypted,occupation,tagsEncrypted,memoEncrypted,rrnEncrypted,rrnHash,customerStatus,importBatchId,createdAt,updatedAt,syncStatus) VALUES ('a','합성 A',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'ACTIVE',NULL,'before','before',1)")
            execSQL("INSERT INTO customers (id,name,phone,birthDate,gender,email,addressEncrypted,occupation,tagsEncrypted,memoEncrypted,rrnEncrypted,rrnHash,customerStatus,importBatchId,createdAt,updatedAt,syncStatus) VALUES ('b','합성 B',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'ACTIVE',NULL,'before','before',0)")
            execSQL("INSERT INTO policies (id,customerId,insurer,productName,policyNumber,planType,premium,paymentCycle,startDate,endDate,status,memoEncrypted,documentId,insuredPeriodEncrypted,paymentPeriodEncrypted,isOwn,createdAt,updatedAt,syncStatus) VALUES ('policy-stable','a','합성보험','합성계약',NULL,NULL,12345,'MONTHLY',NULL,NULL,'ACTIVE',NULL,NULL,NULL,NULL,1,'before','before',0)")
            execSQL("INSERT INTO sync_state (singletonId,cursor,lastSuccessAt,lastError) VALUES (1,987,'before',NULL)")
            execSQL("INSERT INTO sync_outbox (operationId,entityType,entityId,baseJson,patchJson,deleted,createdAt,attemptCount) VALUES ('op-stable','customer','a','{}','{}',0,'before',0)")
            execSQL("INSERT INTO sync_conflicts (id,entityType,entityId,kind,baseJson,localJson,remoteJson,localDeleted,remoteDeleted,createdAt) VALUES ('conflict-stable','customer','a','UPDATE_UPDATE','{}','{}','{}',0,0,'before')")
            close()
        }

        helper.runMigrationsAndValidate("migration-3-4-test", 4, true, *DatabaseMigrations.ALL).apply {
            query("SELECT name,syncStatus FROM customers ORDER BY id").use { cursor ->
                check(cursor.count == 2)
            }
            query("SELECT cursor FROM sync_state WHERE singletonId=1").use { cursor ->
                check(cursor.moveToFirst() && cursor.getLong(0) == 987L)
            }
            query("SELECT operationId FROM sync_outbox WHERE operationId='op-stable'").use { cursor ->
                check(cursor.moveToFirst())
            }
            query("SELECT premium FROM policies WHERE id='policy-stable'").use { cursor ->
                check(cursor.moveToFirst() && cursor.getInt(0) == 12345)
            }
            query("SELECT kind FROM sync_conflicts WHERE id='conflict-stable'").use { cursor ->
                check(cursor.moveToFirst() && cursor.getString(0) == "UPDATE_UPDATE")
            }
            execSQL("INSERT INTO family_relationships (id,customerAId,customerBId,relationAToB,otherDescription,createdAt,updatedAt) VALUES ('edge','a','b','SPOUSE',NULL,'before','before')")
            query("SELECT relationAToB FROM family_relationships WHERE id='edge'").use { cursor ->
                check(cursor.moveToFirst() && cursor.getString(0) == "SPOUSE")
            }
            close()
        }
    }
}
