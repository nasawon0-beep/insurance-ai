package com.insuranceai.android.sync

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.insuranceai.android.InsuranceAiApplication
import java.util.concurrent.TimeUnit

class SyncWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result = try {
        val app = applicationContext as InsuranceAiApplication
        if (app.container.syncRepository.syncOnce()) Result.success() else Result.success()
    } catch (_: SecurityException) {
        // Keystore can require a recent biometric session; retry after the app is unlocked.
        Result.retry()
    } catch (_: Exception) {
        Result.retry()
    }

    companion object {
        private const val NAME = "moadoom-lan-sync"

        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                NAME,
                ExistingPeriodicWorkPolicy.UPDATE,
                request
            )
        }
    }
}
