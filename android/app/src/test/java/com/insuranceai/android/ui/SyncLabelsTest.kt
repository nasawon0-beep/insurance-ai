package com.insuranceai.android.ui

import org.junit.Assert.assertEquals
import org.junit.Test

class SyncLabelsTest {
    @Test
    fun consultationSyncLabelShowsPendingAndDrainedStates() {
        assertEquals("동기화: 1", consultationSyncLabel(1))
        assertEquals("동기화: 0", consultationSyncLabel(0))
    }

    @Test
    fun authenticationExpiryHasActionableStatusWithoutWeakeningAuthentication() {
        assertEquals(
            "앱 잠금 해제 후 동기화를 다시 시도합니다",
            syncFailureStatus(IllegalStateException("cipher", java.security.InvalidKeyException("User not authenticated")))
        )
        assertEquals("실패: network", syncFailureStatus(IllegalStateException("network")))
    }
}
