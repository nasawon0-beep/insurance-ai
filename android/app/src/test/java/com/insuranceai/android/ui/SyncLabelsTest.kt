package com.insuranceai.android.ui

import com.insuranceai.android.data.local.SyncStateEntity
import com.insuranceai.android.security.AuthenticationRequiredException
import com.insuranceai.android.sync.LanSyncHttpException
import java.net.SocketTimeoutException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SyncLabelsTest {
    @Test
    fun consultationSyncLabelShowsPendingAndDrainedStates() {
        assertEquals("PC 전송 대기", consultationSyncLabel(1))
        assertEquals("동기화 완료", consultationSyncLabel(0))
    }

    @Test
    fun authenticationExpiryHasActionableStatusWithoutWeakeningAuthentication() {
        assertEquals(
            "앱 잠금 해제 후 동기화를 다시 시도합니다",
            syncFailureStatus(AuthenticationRequiredException())
        )
        assertEquals("실패: network", syncFailureStatus(IllegalStateException("network")))
    }

    @Test
    fun unauthorizedAndTimeoutHaveDistinctConservativeUserMessages() {
        val unauthorized = syncFailureStatus(LanSyncHttpException(401))
        val timeout = syncFailureStatus(SocketTimeoutException())

        assertTrue(unauthorized.contains("연결이 해제되었거나"))
        assertTrue(unauthorized.contains("다시 확인"))
        assertTrue(timeout.contains("응답 시간"))
        assertFalse(timeout.contains("해제"))
        assertFalse(unauthorized.contains("token", ignoreCase = true))
        assertFalse(unauthorized.contains("UUID", ignoreCase = true))
    }

    @Test
    fun staleTimeoutIsReplacedAfterARealSuccessfulSync() {
        val stale = SyncStateEntity(lastSuccessAt = "before", lastError = "PC 연결 대기 (SocketTimeoutException)")
        val recovered = stale.copy(lastSuccessAt = "after", lastError = null)

        assertEquals("PC 연결 대기 (SocketTimeoutException)", syncStatusFromState(stale))
        assertEquals("동기화 완료", syncStatusFromState(recovered))
    }

    @Test
    fun aGenuineNewestFailureIsStillShown() {
        val failed = SyncStateEntity(lastSuccessAt = "before", lastError = "PC 연결 대기 · 새 요청 실패 (SocketTimeoutException)")

        assertEquals("PC 연결 대기 · 새 요청 실패 (SocketTimeoutException)", syncStatusFromState(failed))
    }
}
