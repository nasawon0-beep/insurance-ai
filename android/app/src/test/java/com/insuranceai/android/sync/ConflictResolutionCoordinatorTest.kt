package com.insuranceai.android.sync

import com.insuranceai.android.data.local.SyncConflictEntity
import com.insuranceai.android.security.AuthenticationRequiredException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConflictResolutionCoordinatorTest {
    @Test
    fun unsupportedMobileRestoreShowsActionableStatusWithoutFalseSuccess() = runTest {
        val fake = FakeActions(failure = ConflictResolutionUnavailableException(
            "PC에 없는 고객은 모바일 값으로 복원할 수 없습니다 · PC 값 선택으로 정리하세요"
        ))
        val coordinator = ConflictResolutionCoordinator(fake)

        coordinator.resolve(conflict, chooseLocal = true) { true }

        assertEquals(
            "PC에 없는 고객은 모바일 값으로 복원할 수 없습니다 · PC 값 선택으로 정리하세요",
            coordinator.state.value.statusByConflict[conflict.id],
        )
    }

    private val conflict = SyncConflictEntity(
        id = "conflict-1",
        entityType = "customer",
        entityId = "customer-1",
        kind = "field",
        baseJson = "{}",
        localJson = "{}",
        remoteJson = "{}",
        localDeleted = false,
        remoteDeleted = false,
    )

    @Test
    fun successRequiresServerConfirmationAndRefresh() = runTest {
        val actions = FakeActions()
        val coordinator = ConflictResolutionCoordinator(actions)

        coordinator.resolve(conflict, chooseLocal = true) { true }

        assertEquals(listOf(true), actions.choices)
        assertEquals("서버 확인 및 재조회 완료", coordinator.state.value.statusByConflict[conflict.id])
        assertFalse(coordinator.state.value.inFlightIds.contains(conflict.id))
    }

    @Test
    fun unresolvedServerResultIsKeptAsFailure() = runTest {
        val coordinator = ConflictResolutionCoordinator(FakeActions(resolved = false))

        coordinator.resolve(conflict, chooseLocal = true) { true }

        assertEquals("미해소 · 서버에서 충돌이 남아 있습니다", coordinator.state.value.statusByConflict[conflict.id])
    }

    @Test
    fun authenticationExpiryPromptsBeforeRetry() = runTest {
        val actions = FakeActions(authenticationFailures = 1)
        val coordinator = ConflictResolutionCoordinator(actions)
        var prompts = 0

        coordinator.resolve(conflict, chooseLocal = false) { prompts++; true }

        assertEquals(1, prompts)
        assertEquals(listOf(false, false), actions.choices)
        assertEquals("서버 확인 및 재조회 완료", coordinator.state.value.statusByConflict[conflict.id])
    }

    @Test
    fun authenticationCancellationDoesNotRetry() = runTest {
        val actions = FakeActions(authenticationFailures = 1)
        val coordinator = ConflictResolutionCoordinator(actions)

        coordinator.resolve(conflict, chooseLocal = true) { false }

        assertEquals(1, actions.choices.size)
        assertEquals("재인증 필요 · 앱 잠금 해제 후 다시 선택하세요", coordinator.state.value.statusByConflict[conflict.id])
    }

    @Test
    fun transportErrorIsShownAndConflictRemainsAvailable() = runTest {
        val coordinator = ConflictResolutionCoordinator(FakeActions(failure = IllegalStateException("private body")))

        coordinator.resolve(conflict, chooseLocal = true) { true }

        assertEquals("해결 실패 · IllegalStateException", coordinator.state.value.statusByConflict[conflict.id])
        assertFalse(coordinator.state.value.inFlightIds.contains(conflict.id))
    }

    @Test
    fun rapidDoubleTapSendsOnlyOneResolution() = runTest {
        val gate = CompletableDeferred<Unit>()
        val actions = FakeActions(gate = gate)
        val coordinator = ConflictResolutionCoordinator(actions)

        val first = async { coordinator.resolve(conflict, chooseLocal = true) { true } }
        while (actions.choices.isEmpty()) testScheduler.runCurrent()
        val second = async { coordinator.resolve(conflict, chooseLocal = true) { true } }
        testScheduler.runCurrent()

        assertEquals(1, actions.choices.size)
        assertTrue(coordinator.state.value.inFlightIds.contains(conflict.id))
        gate.complete(Unit)
        first.await()
        second.await()
        assertEquals(1, actions.choices.size)
    }

    @Test
    fun bothMobileAndPcChoicesAreForwardedExactly() = runTest {
        val actions = FakeActions()
        val coordinator = ConflictResolutionCoordinator(actions)

        coordinator.resolve(conflict.copy(id = "mobile"), chooseLocal = true) { true }
        coordinator.resolve(conflict.copy(id = "pc"), chooseLocal = false) { true }

        assertEquals(listOf(true, false), actions.choices)
    }

    private class FakeActions(
        private val resolved: Boolean = true,
        private var authenticationFailures: Int = 0,
        private val gate: CompletableDeferred<Unit>? = null,
        private val failure: Throwable? = null,
    ) : ConflictResolutionActions {
        val choices = mutableListOf<Boolean>()

        override suspend fun resolveConflict(conflict: SyncConflictEntity, chooseLocal: Boolean): Boolean {
            choices += chooseLocal
            gate?.await()
            if (authenticationFailures > 0) {
                authenticationFailures--
                throw AuthenticationRequiredException()
            }
            failure?.let { throw it }
            return resolved
        }
    }
}
