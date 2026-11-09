package com.insuranceai.android

import org.junit.Assert.assertEquals
import org.junit.Test

class ReleaseIdentityTest {
    @Test
    fun revokedConnectionRecoveryHasMonotonicPackageVersion() {
        assertEquals("com.insuranceai.android", BuildConfig.APPLICATION_ID)
        assertEquals(14, BuildConfig.VERSION_CODE)
        assertEquals("1.0.0-phase14", BuildConfig.VERSION_NAME)
    }
}
