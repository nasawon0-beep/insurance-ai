package com.insuranceai.android.sync

import android.content.Context
import androidx.work.Constraints
import androidx.work.BackoffPolicy
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
    override suspend fun doWork(): Result {
        val app = applicationContext as InsuranceAiApplication
        return try {
            if (app.container.syncRepository.syncOnce()) Result.success() else Result.success()
        } catch (error: Exception) {
            val failure = workerFailure(error, runAttemptCount)
            app.container.syncRepository.recordWorkerFailure(failure.status)
            if (failure.retry) Result.retry() else Result.failure()
        }
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
            val networkType = if (IMMEDIATE_SYNC_WORK_SPEC.requiresConnectedNetwork) {
                NetworkType.CONNECTED
            } else {
                NetworkType.NOT_REQUIRED
            }
            val request = OneTimeWorkRequestBuilder<SyncWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(networkType).build())
                .setBackoffCriteria(
                    BackoffPolicy.EXPONENTIAL,
                    IMMEDIATE_SYNC_WORK_SPEC.initialBackoffSeconds,
                    TimeUnit.SECONDS,
                )
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                NOW_NAME,
                when (IMMEDIATE_SYNC_WORK_SPEC.chainPolicy) {
                    SyncChainPolicy.APPEND_OR_REPLACE -> androidx.work.ExistingWorkPolicy.APPEND_OR_REPLACE
                },
                request
            )
        }
    }
}

class AndroidSyncEnqueuer(context: Context) : SyncEnqueuer {
    private val appContext = context.applicationContext
    override fun enqueue() = SyncWorker.enqueueNow(appContext)
}
