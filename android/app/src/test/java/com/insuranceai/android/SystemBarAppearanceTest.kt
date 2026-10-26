package com.insuranceai.android

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SystemBarAppearanceTest {
    @Test
    fun iconAppearanceFollowsAppSurfaceInsteadOfSystemDarkMode() {
        assertTrue(shouldUseDarkSystemBarIcons(appSurfaceLuminance = 0.96f))
        assertFalse(shouldUseDarkSystemBarIcons(appSurfaceLuminance = 0.08f))
    }
}