package com.insuranceai.android.data.local

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import net.sqlcipher.database.SQLiteDatabase
import net.sqlcipher.database.SupportFactory

@Database(
    entities = [CustomerEntity::class, PolicyEntity::class, ConsultationEntity::class,
        CoverageAnalysisEntity::class, PolicyCoverageEntity::class, SyncOutboxEntity::class, SyncConflictEntity::class,
        SyncStateEntity::class, SyncTombstoneAckEntity::class],
    version = 3,
    exportSchema = true
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun customerDao(): CustomerDao
    abstract fun policyDao(): PolicyDao
    abstract fun consultationDao(): ConsultationDao
    abstract fun coverageAnalysisDao(): CoverageAnalysisDao
    abstract fun policyCoverageDao(): PolicyCoverageDao
    abstract fun syncDao(): SyncDao

    companion object {
        const val DB_NAME = "insurance_ai.db"

        fun create(context: Context, passphrase: ByteArray): AppDatabase {
            SQLiteDatabase.loadLibs(context)
            val factory = SupportFactory(passphrase)
            return Room.databaseBuilder(context, AppDatabase::class.java, DB_NAME)
                .openHelperFactory(factory)
                .addMigrations(*DatabaseMigrations.ALL)
                .build()
        }
    }
}

object DatabaseMigrations {
    private val MIGRATION_1_2 = object : Migration(1, 2) {
        override fun migrate(db: SupportSQLiteDatabase) {
            db.execSQL("CREATE TABLE IF NOT EXISTS `coverage_analysis_results` (`id` TEXT NOT NULL, `customerId` TEXT NOT NULL, `summaryJsonEncrypted` TEXT NOT NULL, `generatedAt` TEXT NOT NULL, `sourceHash` TEXT, `updatedAt` TEXT NOT NULL, PRIMARY KEY(`id`), FOREIGN KEY(`customerId`) REFERENCES `customers`(`id`) ON UPDATE NO ACTION ON DELETE CASCADE)")
            db.execSQL("CREATE INDEX IF NOT EXISTS `index_coverage_analysis_results_customerId` ON `coverage_analysis_results` (`customerId`)")
            db.execSQL("CREATE TABLE IF NOT EXISTS `sync_outbox` (`localId` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, `operationId` TEXT NOT NULL, `entityType` TEXT NOT NULL, `entityId` TEXT NOT NULL, `baseJson` TEXT NOT NULL, `patchJson` TEXT NOT NULL, `deleted` INTEGER NOT NULL, `createdAt` TEXT NOT NULL, `attemptCount` INTEGER NOT NULL)")
            db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS `index_sync_outbox_operationId` ON `sync_outbox` (`operationId`)")
            db.execSQL("CREATE TABLE IF NOT EXISTS `sync_conflicts` (`id` TEXT NOT NULL, `entityType` TEXT NOT NULL, `entityId` TEXT NOT NULL, `kind` TEXT NOT NULL, `baseJson` TEXT NOT NULL, `localJson` TEXT NOT NULL, `remoteJson` TEXT NOT NULL, `localDeleted` INTEGER NOT NULL, `remoteDeleted` INTEGER NOT NULL, `createdAt` TEXT NOT NULL, PRIMARY KEY(`id`))")
            db.execSQL("CREATE TABLE IF NOT EXISTS `sync_state` (`singletonId` INTEGER NOT NULL, `cursor` INTEGER NOT NULL, `lastSuccessAt` TEXT, `lastError` TEXT, PRIMARY KEY(`singletonId`))")
            db.execSQL("CREATE TABLE IF NOT EXISTS `sync_tombstone_acks` (`cursor` INTEGER NOT NULL, PRIMARY KEY(`cursor`))")
        }
    }

    private val MIGRATION_2_3 = object : Migration(2, 3) {
        override fun migrate(db: SupportSQLiteDatabase) {
            db.execSQL("ALTER TABLE `policies` ADD COLUMN `documentStatus` TEXT")
            db.execSQL("ALTER TABLE `policies` ADD COLUMN `sourceDocumentType` TEXT")
            db.execSQL("CREATE TABLE IF NOT EXISTS `policy_coverages` (`id` TEXT NOT NULL, `customerId` TEXT NOT NULL, `policyId` TEXT, `insurer` TEXT, `productName` TEXT, `riderName` TEXT, `standardName` TEXT, `amount` INTEGER, `amountText` TEXT, `amountStatus` TEXT, `documentStatus` TEXT, `startDate` TEXT, `endDate` TEXT, `rawPayloadEncrypted` TEXT NOT NULL, `updatedAt` TEXT NOT NULL, PRIMARY KEY(`id`), FOREIGN KEY(`customerId`) REFERENCES `customers`(`id`) ON UPDATE NO ACTION ON DELETE CASCADE)")
            db.execSQL("CREATE INDEX IF NOT EXISTS `index_policy_coverages_customerId` ON `policy_coverages` (`customerId`)")
            db.execSQL("CREATE INDEX IF NOT EXISTS `index_policy_coverages_policyId` ON `policy_coverages` (`policyId`)")
            // v2 discarded these change types. Replay the immutable remote log without
            // deleting local edits, outbox rows, conflicts, or encrypted records.
            db.execSQL("UPDATE `sync_state` SET `cursor` = 0")
        }
    }

    val ALL = arrayOf(MIGRATION_1_2, MIGRATION_2_3)
}
