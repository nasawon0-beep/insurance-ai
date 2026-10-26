package com.insuranceai.android.ui

import android.content.ComponentName
import android.content.Intent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.assertHeightIsAtLeast
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.util.concurrent.atomic.AtomicBoolean

class CustomersScreenAccessibilityTest {
    @get:Rule
    val composeRule = createEmptyComposeRule()

    @Test
    fun pcSyncButtonKeeps48DpTouchTargetAtSmallFontScale() {
        assertPcSyncButtonAtFontScale(0.85f)
    }

    @Test
    fun pcSyncButtonKeeps48DpTouchTargetAtLargeFontScale() {
        assertPcSyncButtonAtFontScale(2.0f)
    }

    private fun assertPcSyncButtonAtFontScale(fontScale: Float) {
        val clicked = AtomicBoolean(false)
        val targetContext = InstrumentationRegistry.getInstrumentation().targetContext
        val intent = Intent(Intent.ACTION_MAIN).apply {
            component = ComponentName(targetContext.packageName, ComponentActivity::class.java.name)
        }
        ActivityScenario.launch<ComponentActivity>(intent).use { scenario ->
            scenario.onActivity { activity ->
                activity.setContent {
                    val density = LocalDensity.current
                    CompositionLocalProvider(
                        LocalDensity provides Density(density.density, fontScale),
                    ) {
                        PcSyncSummaryCard(
                            pendingCount = 3,
                            syncMessage = "최근 동기화 성공: 2026-10-25T01:02:03Z",
                            onOpenSync = { clicked.set(true) },
                        )
                    }
                }
            }

            composeRule.onNodeWithText("PC 동기화 열기")
                .assertIsDisplayed()
                .assertHeightIsAtLeast(48.dp)
                .performClick()
            composeRule.runOnIdle { assertTrue(clicked.get()) }
        }
    }
}
