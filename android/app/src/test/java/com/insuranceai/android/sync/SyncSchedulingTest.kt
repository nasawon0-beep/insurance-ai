package com.insuranceai.android.sync

import com.insuranceai.android.security.AuthenticationRequiredException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.yield
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class SyncSchedulingTest {
    @Test
    fun successfulCommitEnqueuesConnectedOneTimeWork() = runTest {
        val enqueuer = FakeEnqueuer()
        val dispatcher = PostCommitSyncDispatcher(enqueuer)

        val result = dispatcher.run { "saved" }

        assertEquals("saved", result.value)
        assertTrue(result.syncEnqueued)
        assertEquals(1, enqueuer.calls)
        assertTrue(IMMEDIATE_SYNC_WORK_SPEC.requiresConnectedNetwork)
        assertEquals(SyncChainPolicy.APPEND_OR_REPLACE, IMMEDIATE_SYNC_WORK_SPEC.chainPolicy)
    }

    @Test
    fun failedTransactionDoesNotEnqueue() = runTest {
        val enqueuer = FakeEnqueuer()
        val dispatcher = PostCommitSyncDispatcher(enqueuer)

        assertThrows(IllegalStateException::class.java) {
            runTest { dispatcher.run<String> { error("transaction failed") } }
        }
        assertEquals(0, enqueuer.calls)
    }

    @Test
    fun enqueueFailureKeepsCommittedValueAndReportsQueueFailure() = runTest {
        val dispatcher = PostCommitSyncDispatcher(FakeEnqueuer(fail = true))

        val result = dispatcher.run { "stored locally" }

        assertEquals("stored locally", result.value)
        assertFalse(result.syncEnqueued)
    }

    @Test
    fun rapidConcurrentSavesAreAllScheduledWithoutReplaceCancellation() = runTest {
        val enqueuer = FakeEnqueuer()
        val dispatcher = PostCommitSyncDispatcher(enqueuer)

        (1..20).map { value -> async { dispatcher.run { value } } }.awaitAll()

        assertEquals(20, enqueuer.calls)
        assertEquals(SyncChainPolicy.APPEND_OR_REPLACE, IMMEDIATE_SYNC_WORK_SPEC.chainPolicy)
    }

    @Test
    fun authenticationExpiryStopsUntilExplicitAuthenticationWhileOfflineUsesBoundedRetry() {
        val offline = workerFailure(IllegalStateException("connection refused"))
        val auth = workerFailure(AuthenticationRequiredException())

        assertTrue(offline.retry)
        assertFalse(auth.retry)
        assertTrue(auth.status.contains("재인증 필요"))
        assertFalse(auth.status.contains("User not authenticated"))
        assertEquals(BackoffMode.EXPONENTIAL, IMMEDIATE_SYNC_WORK_SPEC.backoffMode)
        assertTrue(IMMEDIATE_SYNC_WORK_SPEC.initialBackoffSeconds >= 10)
        assertTrue(IMMEDIATE_SYNC_WORK_SPEC.initialBackoffSeconds <= 60)
    }

    @Test
    fun networkRetryStopsAtExplicitRunAttemptBoundaryAndAllowsFutureNewWork() {
        val error = IllegalStateException("connection refused")
        val lastRetry = workerFailure(error, runAttemptCount = MAX_SYNC_RUN_ATTEMPTS - 2)
        val terminal = workerFailure(error, runAttemptCount = MAX_SYNC_RUN_ATTEMPTS - 1)

        assertTrue(lastRetry.retry)
        assertFalse(terminal.retry)
        assertTrue(terminal.status.contains("자동 재시도 ${MAX_SYNC_RUN_ATTEMPTS}회 종료"))
        assertTrue(terminal.status.contains("다시 시도"))
    }

    @Test
    fun revokedCredentialStopsAutomaticRetryAndRequiresUserAction() {
        val revoked = workerFailure(LanSyncHttpException(401))

        assertFalse(revoked.retry)
        assertTrue(revoked.status.contains("등록 해제"))
    }

    @Test
    fun successfulAuthenticationEnqueuesResumeWork() {
        val enqueuer = FakeEnqueuer()

        SyncResumeCoordinator(enqueuer).resumeAfterAuthentication()

        assertEquals(1, enqueuer.calls)
    }

    @Test
    fun foregroundResumeRequiresUnlockedSessionAndRegisteredPc() {
        assertTrue(shouldResumeSyncOnForeground(sessionValid = true, connectionConfigured = true))
        assertFalse(shouldResumeSyncOnForeground(sessionValid = false, connectionConfigured = true))
        assertFalse(shouldResumeSyncOnForeground(sessionValid = true, connectionConfigured = false))
    }

    @Test
    fun firstAuthenticationSuccessStartsDiscoveryWithoutWaitingForAnotherResume() = runTest {
        val calls = mutableListOf<String>()
        val coordinator = ForegroundSyncLifecycleCoordinator(
            reconnect = {
                calls += "discover"
                AutoReconnectResult.CONNECTED
            },
            resumeSync = { calls += "sync" },
        )

        val started = coordinator.resumeWithResult(
            sessionValid = true,
            connectionConfigured = true,
        )

        assertEquals(ForegroundSyncResult.ENQUEUED, started)
        assertEquals(listOf("discover", "sync"), calls)
    }

    @Test
    fun overlappingForegroundAndAuthenticationCoalesceButLaterForegroundCanRun() = runTest {
        val entered = CompletableDeferred<Unit>()
        val release = CompletableDeferred<Unit>()
        var syncCalls = 0
        val coordinator = ForegroundSyncLifecycleCoordinator(
            reconnect = {
                entered.complete(Unit)
                release.await()
                AutoReconnectResult.CONNECTED
            },
            resumeSync = { syncCalls++ },
        )

        val first = async { coordinator.resumeWithResult(true, true) }
        entered.await()
        val overlapping = coordinator.resumeWithResult(true, true)
        release.complete(Unit)

        assertEquals(ForegroundSyncResult.COALESCED, overlapping)
        assertEquals(ForegroundSyncResult.ENQUEUED, first.await())
        assertEquals(1, syncCalls)
        assertEquals(ForegroundSyncResult.ENQUEUED, coordinator.resumeWithResult(true, true))
        assertEquals(2, syncCalls)
    }

    @Test
    fun discoveryAndAuthenticationFailuresNeverEnqueueSync() = runTest {
        for ((reconnectResult, expected) in listOf(
            AutoReconnectResult.DISCOVERY_FAILED to ForegroundSyncResult.DISCOVERY_FAILED,
            AutoReconnectResult.AUTHENTICATION_FAILED to ForegroundSyncResult.AUTHENTICATION_FAILED,
        )) {
            var syncCalls = 0
            val coordinator = ForegroundSyncLifecycleCoordinator(
                reconnect = { reconnectResult },
                resumeSync = { syncCalls++ },
            )

            assertEquals(expected, coordinator.resumeWithResult(true, true))
            assertEquals(0, syncCalls)
        }
    }

    @Test
    fun periodicAndImmediateRunsCannotEnterSyncTogether() = runTest {
        val gate = SyncRunGate()
        var active = 0
        var maximumActive = 0

        (1..20).map {
            async {
                gate.run {
                    active++
                    maximumActive = maxOf(maximumActive, active)
                    yield()
                    active--
                }
            }
        }.awaitAll()

        assertEquals(1, maximumActive)
    }

    @Test
    fun oldAddressResultCannotFinishAfterVerifiedAddressUpdateStarts() = runTest {
        val gate = SyncRunGate()
        val releaseOldAddress = kotlinx.coroutines.CompletableDeferred<Unit>()
        val oldAddressStarted = kotlinx.coroutines.CompletableDeferred<Unit>()
        var oldAddressActive = false
        var overlapDetected = false

        val oldAddress = async {
            gate.run {
                oldAddressActive = true
                oldAddressStarted.complete(Unit)
                releaseOldAddress.await()
                oldAddressActive = false
            }
        }
        oldAddressStarted.await()
        val verifiedAddressUpdate = async {
            gate.run {
                if (oldAddressActive) overlapDetected = true
            }
        }
        testScheduler.runCurrent()

        assertFalse(verifiedAddressUpdate.isCompleted)
        releaseOldAddress.complete(Unit)
        oldAddress.await()
        verifiedAddressUpdate.await()
        assertFalse(overlapDetected)
    }

    private class FakeEnqueuer(private val fail: Boolean = false) : SyncEnqueuer {
        var calls = 0
        override fun enqueue() {
            calls++
            if (fail) error("scheduler unavailable")
        }
    }
}
