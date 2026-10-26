package com.insuranceai.android.sync

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RecoveryCoordinatorTest {
    @Test
    fun authenticationApprovalThenCorrectCodeRestoresSameConnection() = runTest {
        val actions = FakeRecoveryActions()
        val coordinator = RecoveryCoordinator(actions)

        coordinator.recover(RecoveryRequest("123456")) { true }

        assertEquals(listOf("prepare", "recover"), actions.calls)
        assertTrue(coordinator.state.value.recovered)
        assertEquals("기존 연결 복구 완료 · 미전송 자료를 유지했습니다", coordinator.state.value.status)
    }

    @Test
    fun cancelledAuthenticationMakesNoRecoveryRequest() = runTest {
        val actions = FakeRecoveryActions(prepareRequiresAuthentication = true)
        val coordinator = RecoveryCoordinator(actions)

        coordinator.recover(RecoveryRequest("123456")) { false }

        assertEquals(listOf("prepare"), actions.calls)
        assertFalse(coordinator.state.value.recovered)
        assertFalse(coordinator.state.value.inFlight)
    }

    @Test
    fun networkTimeoutKeepsCodeRetryableAndDoesNotClaimRevocation() = runTest {
        val actions = FakeRecoveryActions(failure = java.net.SocketTimeoutException())
        val coordinator = RecoveryCoordinator(actions)

        coordinator.recover(RecoveryRequest("123456")) { true }

        assertFalse(coordinator.state.value.codeConsumed)
        assertTrue(coordinator.state.value.status.contains("응답 시간"))
        assertFalse(coordinator.state.value.status.contains("해제 확인"))
    }

    private class FakeRecoveryActions(
        private var prepareRequiresAuthentication: Boolean = false,
        private val failure: Throwable? = null,
    ) : RecoveryActions {
        val calls = mutableListOf<String>()

        override suspend fun prepareRecoveryStorage() {
            calls += "prepare"
            if (prepareRequiresAuthentication) throw com.insuranceai.android.security.AuthenticationRequiredException()
        }

        override suspend fun recoverAndPromote(request: RecoveryRequest) {
            calls += "recover"
            failure?.let { throw it }
        }
    }
}