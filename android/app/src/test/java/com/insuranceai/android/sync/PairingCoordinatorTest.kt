package com.insuranceai.android.sync

import com.insuranceai.android.security.AuthenticationRequiredException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingCoordinatorTest {
    private val request = PairingRequest(
        endpoint = "https://192.0.2.1:18443",
        certificatePin = "a".repeat(64),
        code = "123456",
        deviceName = "test phone",
    )

    @Test
    fun successReportsSavedThenSynced() = runTest {
        val actions = FakeActions()
        val coordinator = PairingCoordinator(actions)

        coordinator.pair(request) { true }

        assertEquals(listOf("prepare", "pairAndSave", "sync"), actions.calls)
        assertTrue(coordinator.state.value.paired)
        assertFalse(coordinator.state.value.inFlight)
        assertEquals("페어링 정보 저장 및 최초 동기화 완료", coordinator.state.value.status)
    }

    @Test
    fun authenticationExpiryPromptsBeforeNetworkAndCancellationConsumesNoCode() = runTest {
        val actions = FakeActions(prepareAuthFailures = 1)
        val coordinator = PairingCoordinator(actions)
        var prompts = 0

        coordinator.pair(request) { prompts++; false }

        assertEquals(1, prompts)
        assertEquals(listOf("prepare"), actions.calls)
        assertFalse(coordinator.state.value.inFlight)
        assertFalse(coordinator.state.value.codeConsumed)
        assertEquals("기기 인증이 취소되어 페어링을 시작하지 않았습니다", coordinator.state.value.status)
    }

    @Test
    fun authenticationRetrySucceedsBeforePairRequest() = runTest {
        val actions = FakeActions(prepareAuthFailures = 1)
        val coordinator = PairingCoordinator(actions)

        coordinator.pair(request) { true }

        assertEquals(listOf("prepare", "prepare", "pairAndSave", "sync"), actions.calls)
        assertEquals("페어링 정보 저장 및 최초 동기화 완료", coordinator.state.value.status)
    }

    @Test
    fun serverSuccessThenLocalSaveFailurePreservesFirstErrorAndBlocksSameCode() = runTest {
        val actions = FakeActions(localSaveFailure = true)
        val coordinator = PairingCoordinator(actions)

        coordinator.pair(request) { true }
        val firstStatus = coordinator.state.value.status
        coordinator.onCodeChanged("1")
        coordinator.pair(request) { true }

        assertEquals(1, actions.calls.count { it == "pairAndSave" })
        assertTrue(coordinator.state.value.codeConsumed)
        assertTrue(coordinator.state.value.requiresNewCode)
        assertEquals(firstStatus, coordinator.state.value.status)
        assertTrue(firstStatus.contains("PC 페어링은 완료"))
        assertTrue(firstStatus.contains("새 일회용 코드"))
        assertFalse(firstStatus.contains("token secret"))
    }

    @Test
    fun firstSyncFailureKeepsPairingSuccessAndAllowsSyncRetry() = runTest {
        val actions = FakeActions(syncFailure = IllegalStateException("private response"))
        val coordinator = PairingCoordinator(actions)

        coordinator.pair(request) { true }

        assertTrue(coordinator.state.value.paired)
        assertFalse(coordinator.state.value.inFlight)
        assertTrue(coordinator.state.value.status.startsWith("페어링 정보 저장 완료"))
        assertFalse(coordinator.state.value.status.contains("페어링 실패"))
        assertFalse(coordinator.state.value.status.contains("private response"))
    }

    @Test
    fun rapidDoubleSubmitMakesOnlyOnePairRequestAndInFlightRecovers() = runTest {
        val gate = CompletableDeferred<Unit>()
        val actions = FakeActions(pairGate = gate)
        val coordinator = PairingCoordinator(actions)

        val first = async { coordinator.pair(request) { true } }
        while (actions.calls.count { it == "pairAndSave" } == 0) testScheduler.runCurrent()
        val second = async { coordinator.pair(request) { true } }
        testScheduler.runCurrent()

        assertEquals(1, actions.calls.count { it == "pairAndSave" })
        gate.complete(Unit)
        first.await()
        second.await()
        assertFalse(coordinator.state.value.inFlight)
    }

    @Test
    fun differentNewCodeCanStartAfterConsumedCodeFailure() = runTest {
        val actions = FakeActions(localSaveFailure = true)
        val coordinator = PairingCoordinator(actions)
        coordinator.pair(request) { true }

        coordinator.onCodeChanged("654321")
        actions.localSaveFailure = false
        coordinator.pair(request.copy(code = "654321")) { true }

        assertEquals(2, actions.calls.count { it == "pairAndSave" })
        assertTrue(coordinator.state.value.paired)
    }

    private class FakeActions(
        private var prepareAuthFailures: Int = 0,
        var localSaveFailure: Boolean = false,
        private val syncFailure: Throwable? = null,
        private val pairGate: CompletableDeferred<Unit>? = null,
    ) : PairingActions {
        val calls = mutableListOf<String>()

        override suspend fun prepareCredentialStorage() {
            calls += "prepare"
            if (prepareAuthFailures > 0) {
                prepareAuthFailures--
                throw AuthenticationRequiredException()
            }
        }

        override suspend fun pairAndSave(request: PairingRequest) {
            calls += "pairAndSave"
            pairGate?.await()
            if (localSaveFailure) throw PairingLocalSaveException(IllegalStateException("token secret"))
        }

        override suspend fun syncOnce(): Boolean {
            calls += "sync"
            syncFailure?.let { throw it }
            return true
        }
    }
}
