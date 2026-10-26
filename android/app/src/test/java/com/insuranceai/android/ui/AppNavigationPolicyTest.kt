package com.insuranceai.android.ui

import org.junit.Assert.assertTrue
import org.junit.Test

class AppNavigationPolicyTest {
    @Test
    fun authenticationExpiryKeepsInterruptedRouteMountedBehindLockScreen() {
        val policy = authenticationUiPolicy(authenticationRequired = true)

        assertTrue(policy.showLockScreen)
        assertTrue(policy.keepNavigationMounted)
    }
}
