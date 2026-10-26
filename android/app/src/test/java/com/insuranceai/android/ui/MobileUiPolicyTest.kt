package com.insuranceai.android.ui

import com.insuranceai.android.shouldProtectScreen
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MobileUiPolicyTest {
    @Test
    fun customerSyncStatesUseMeaningfulKoreanLabels() {
        assertEquals("동기화 완료", customerSyncLabel(0))
        assertEquals("PC 전송 대기", customerSyncLabel(1))
        assertEquals("동기화 충돌", customerSyncLabel(2))
        assertEquals("동기화 상태 확인 필요", customerSyncLabel(99))
    }

    @Test
    fun pendingSummaryDoesNotClaimSuccessFromZeroAlone() {
        assertEquals("PC 전송 대기 2건", pendingSyncSummary(2))
        assertEquals("전송 대기 없음", pendingSyncSummary(0))
    }

    @Test
    fun captureIsAllowedOnlyForDebugBuilds() {
        assertFalse(shouldProtectScreen(isDebug = true))
        assertTrue(shouldProtectScreen(isDebug = false))
    }
}
