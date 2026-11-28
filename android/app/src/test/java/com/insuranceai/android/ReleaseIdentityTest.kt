package com.insuranceai.android

import org.junit.Assert.assertEquals
import org.junit.Test

class ReleaseIdentityTest {
    @Test
    fun storePackageIsSeparateFromLegacyInstalledApp() {
        assertEquals("com.flowon.moadoom", BuildConfig.APPLICATION_ID)
        assertEquals(1, BuildConfig.VERSION_CODE)
        assertEquals("1.0.0", BuildConfig.VERSION_NAME)
    }
}
