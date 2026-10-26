package com.insuranceai.android.sync

import com.insuranceai.android.security.AuthenticationRequiredException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class AddressContinuityTest {
    private val request = AddressContinuityRequest(
        endpoint = "https://192.0.2.2:55902",
        certificatePin = "b".repeat(64),
        certificateConfirmedFromPc = true,
    )

    @Test
    fun requiresExplicitPcCertificateConfirmationBeforeAnyCredentialUse() = runTest {
        val actions = FakeActions()
        val coordinator = AddressContinuityCoordinator(actions)

        coordinator.update(request.copy(certificateConfirmedFromPc = false)) { true }

        assertEquals(0, actions.calls)
        assertTrue(coordinator.state.value.status.contains("먼저 확인"))
    }

    @Test
    fun authenticationExpiryPromptsAndRetriesOnce() = runTest {
        val actions = FakeActions(authenticationFailures = 1)
        val coordinator = AddressContinuityCoordinator(actions)
        var prompts = 0

        coordinator.update(request) { prompts++; true }

        assertEquals(1, prompts)
        assertEquals(2, actions.calls)
        assertTrue(coordinator.state.value.status.contains("연결 주소 갱신됨"))
    }

    @Test
    fun cancellationPreservesOldConnection() = runTest {
        val actions = FakeActions(authenticationFailures = 1)
        val coordinator = AddressContinuityCoordinator(actions)

        coordinator.update(request) { false }

        assertEquals(1, actions.calls)
        assertTrue(coordinator.state.value.status.contains("기존 연결 정보를 유지"))
    }

    @Test
    fun wrongAddressPinOrServerFailurePreservesOldConnection() = runTest {
        val actions = FakeActions(failure = IllegalStateException("pin mismatch"))
        val coordinator = AddressContinuityCoordinator(actions)

        coordinator.update(request) { true }

        assertEquals(1, actions.calls)
        assertEquals("주소 갱신 실패 · 기존 연결 정보를 유지했습니다 (IllegalStateException)", coordinator.state.value.status)
    }

    @Test
    fun rapidDoubleSubmitRunsOnlyOneContinuityProbe() = runTest {
        val gate = CompletableDeferred<Unit>()
        val actions = FakeActions(gate = gate)
        val coordinator = AddressContinuityCoordinator(actions)

        val first = async { coordinator.update(request) { true } }
        while (actions.calls == 0) testScheduler.runCurrent()
        val second = async { coordinator.update(request) { true } }
        testScheduler.runCurrent()

        assertEquals(1, actions.calls)
        assertTrue(coordinator.state.value.inFlight)
        gate.complete(Unit)
        first.await()
        second.await()
        assertEquals(1, actions.calls)
        assertFalse(coordinator.state.value.inFlight)
    }

    @Test
    fun continuityRejectsDifferentDeviceIdentity() {
        assertThrows(IllegalArgumentException::class.java) {
            validateContinuityProof(
                expectedDeviceId = "old-device",
                localConflictIds = setOf("conflict-1"),
                proof = ContinuityProof("authenticated-device", "new-device", setOf("conflict-1")),
            )
        }
    }

    @Test
    fun continuityRejectsMissingOwnedConflict() {
        assertThrows(IllegalArgumentException::class.java) {
            validateContinuityProof(
                expectedDeviceId = null,
                localConflictIds = setOf("conflict-1"),
                proof = ContinuityProof("authenticated-device", "old-device", emptySet()),
            )
        }
    }

    @Test
    fun continuityAcceptsLegacySettingsOnlyWhenOldTokenOwnsEveryLocalConflict() {
        validateContinuityProof(
            expectedDeviceId = null,
            localConflictIds = setOf("conflict-1"),
            proof = ContinuityProof("authenticated-device", "old-device", setOf("conflict-1", "conflict-2")),
        )
    }

    private class FakeActions(
        private var authenticationFailures: Int = 0,
        private val failure: Throwable? = null,
        private val gate: CompletableDeferred<Unit>? = null,
    ) : AddressContinuityActions {
        var calls = 0

        override suspend fun verifyAndUpdateAddress(request: AddressContinuityRequest) {
            calls++
            gate?.await()
            if (authenticationFailures > 0) {
                authenticationFailures--
                throw AuthenticationRequiredException()
            }
            failure?.let { throw it }
        }
    }
}
