package com.insuranceai.android.data.local

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import net.sqlcipher.database.SQLiteDatabase
import net.sqlcipher.database.SupportFactory

@Database(
    entities = [CustomerEntity::class, PolicyEntity::class, ConsultationEntity::class],
    version = 1,
    exportSchema = true
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun customerDao(): CustomerDao
    abstract fun policyDao(): PolicyDao
    abstract fun consultationDao(): ConsultationDao

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
    val ALL = emptyArray<androidx.room.migration.Migration>()
}
