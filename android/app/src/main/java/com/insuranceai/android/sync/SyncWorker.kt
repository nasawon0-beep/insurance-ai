package com.insuranceai.android.sync

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
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
        val app = applicationContext as InsuranceAiApplication
        runCatching { app.container.syncRepository.recordBlocked("앱 잠금 해제 후 동기화를 다시 시도합니다") }
        Result.retry()
    } catch (error: Exception) {
        val app = applicationContext as InsuranceAiApplication
        runCatching { app.container.syncRepository.recordBlocked("재연결 대기: ${error.javaClass.simpleName}") }
        Result.retry()
    }

    companion object {
        private const val NAME = "moadoom-lan-sync"
        private const val NOW_NAME = "moadoom-lan-sync-now"

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

        fun enqueueNow(context: Context) {
            val request = OneTimeWorkRequestBuilder<SyncWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                NOW_NAME,
                androidx.work.ExistingWorkPolicy.REPLACE,
                request
            )
        }
    }
}
