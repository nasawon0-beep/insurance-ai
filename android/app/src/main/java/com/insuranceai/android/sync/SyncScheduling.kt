package com.insuranceai.android.sync

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

enum class SyncChainPolicy { APPEND_OR_REPLACE }
enum class BackoffMode { EXPONENTIAL }

data class ImmediateSyncWorkSpec(
    val requiresConnectedNetwork: Boolean,
    val chainPolicy: SyncChainPolicy,
    val backoffMode: BackoffMode,
    val initialBackoffSeconds: Long,
)

val IMMEDIATE_SYNC_WORK_SPEC = ImmediateSyncWorkSpec(
    requiresConnectedNetwork = true,
    chainPolicy = SyncChainPolicy.APPEND_OR_REPLACE,
    backoffMode = BackoffMode.EXPONENTIAL,
    initialBackoffSeconds = 30,
)

interface SyncEnqueuer {
    fun enqueue()
}

data class PostCommitResult<T>(val value: T, val syncEnqueued: Boolean)

/** Enqueues only after the caller's local transaction has completed successfully. */
class PostCommitSyncDispatcher(private val enqueuer: SyncEnqueuer) {
    suspend fun <T> run(commit: suspend () -> T): PostCommitResult<T> {
        val value = commit()
        val enqueued = runCatching { enqueuer.enqueue() }.isSuccess
        return PostCommitResult(value, enqueued)
    }
}

class SyncResumeCoordinator(private val enqueuer: SyncEnqueuer) {
    fun resumeAfterAuthentication() = enqueuer.enqueue()
}

fun shouldResumeSyncOnForeground(sessionValid: Boolean, connectionConfigured: Boolean): Boolean =
    sessionValid && connectionConfigured

enum class ForegroundSyncResult {
    ENQUEUED,
    COALESCED,
    DISCOVERY_FAILED,
    AUTHENTICATION_FAILED,
    NOT_READY,
}

class ForegroundSyncLifecycleCoordinator(
    private val reconnect: suspend () -> AutoReconnectResult,
    private val resumeSync: () -> Unit,
) {
    private val mutex = Mutex()

    suspend fun resume(sessionValid: Boolean, connectionConfigured: Boolean): Boolean =
        resumeWithResult(sessionValid, connectionConfigured) == ForegroundSyncResult.ENQUEUED

    suspend fun resumeWithResult(sessionValid: Boolean, connectionConfigured: Boolean): ForegroundSyncResult {
        if (!shouldResumeSyncOnForeground(sessionValid, connectionConfigured)) {
            return ForegroundSyncResult.NOT_READY
        }
        if (!mutex.tryLock()) return ForegroundSyncResult.COALESCED
        try {
            val result = reconnect()
            return when (result) {
                AutoReconnectResult.CONNECTED,
                AutoReconnectResult.VERIFIED_EXISTING_ENDPOINT -> {
                    resumeSync()
                    ForegroundSyncResult.ENQUEUED
                }
                AutoReconnectResult.COALESCED -> ForegroundSyncResult.COALESCED
                AutoReconnectResult.AUTHENTICATION_FAILED -> ForegroundSyncResult.AUTHENTICATION_FAILED
                AutoReconnectResult.DISCOVERY_FAILED -> ForegroundSyncResult.DISCOVERY_FAILED
            }
        } finally {
            mutex.unlock()
        }
    }
}

class SyncRunGate {
    private val mutex = Mutex()
    suspend fun <T> run(block: suspend () -> T): T = mutex.withLock { block() }
}

data class WorkerFailure(val status: String, val retry: Boolean)

const val MAX_SYNC_RUN_ATTEMPTS = 3

fun workerFailure(error: Throwable, runAttemptCount: Int = 0): WorkerFailure =
    if (error.requiresUserAuthentication()) {
        WorkerFailure("재인증 필요 · 앱 잠금 해제 후 자동 동기화를 재개합니다", retry = false)
    } else if (error is LanSyncHttpException && error.statusCode == 401) {
        WorkerFailure("이 기기는 PC에서 등록 해제되었거나 인증 정보가 만료되었습니다 · 자동 재시도를 중단합니다", retry = false)
    } else if (runAttemptCount >= MAX_SYNC_RUN_ATTEMPTS - 1) {
        WorkerFailure(
            "PC 연결 실패 · 자동 재시도 ${MAX_SYNC_RUN_ATTEMPTS}회 종료 · 앱을 다시 열거나 사용자가 다시 시도하면 재개합니다 (${error.javaClass.simpleName})",
            retry = false,
        )
    } else {
        WorkerFailure("PC 연결 대기 · 네트워크가 연결되면 다시 시도합니다 (${error.javaClass.simpleName})", retry = true)
    }
