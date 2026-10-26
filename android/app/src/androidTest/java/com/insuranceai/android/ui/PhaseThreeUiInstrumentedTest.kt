package com.insuranceai.android.ui

import android.content.ComponentName
import android.content.Intent
import android.graphics.Bitmap
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.Text
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.unit.Density
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import com.insuranceai.android.applySystemBarAppearance
import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.PolicyCoverageEntity
import com.insuranceai.android.data.local.PolicyEntity
import androidx.core.view.WindowCompat
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.io.FileOutputStream

class PhaseThreeUiInstrumentedTest {
    @get:Rule
    val composeRule = createEmptyComposeRule()

    @Test
    fun appWindowControlsLightAndDarkSystemBarIconAppearance() {
        launchActivity().use { scenario ->
            scenario.onActivity { activity ->
                val controller = WindowCompat.getInsetsController(activity.window, activity.window.decorView)
                applySystemBarAppearance(activity.window, useDarkIcons = true)
                assertTrue(controller.isAppearanceLightStatusBars)
                assertTrue(controller.isAppearanceLightNavigationBars)

                applySystemBarAppearance(activity.window, useDarkIcons = false)
                assertFalse(controller.isAppearanceLightStatusBars)
                assertFalse(controller.isAppearanceLightNavigationBars)
            }
        }
    }

    @Test
    fun capturesOppositeAppSurfaceSystemBarAppearancesWithoutGlobalSettingChanges() {
        launchActivity().use { scenario ->
            scenario.onActivity { activity ->
                WindowCompat.setDecorFitsSystemWindows(activity.window, false)
                activity.setContent {
                    Box(Modifier.fillMaxSize().background(Color(0xFFF7F5EF)).statusBarsPadding()) {
                        Text("라이트 앱 표면 · 어두운 상태바 아이콘")
                    }
                }
            }
            composeRule.waitForIdle()
            scenario.onActivity { activity -> applySystemBarAppearance(activity.window, useDarkIcons = true) }
            composeRule.waitForIdle()
            captureFullScreen("01-statusbar-light-app-dark-icons.png")

            scenario.onActivity { activity ->
                activity.setContent {
                    Box(Modifier.fillMaxSize().background(Color(0xFF202124)).statusBarsPadding()) {
                        Text("다크 앱 표면 · 밝은 상태바 아이콘", color = Color.White)
                    }
                }
            }
            composeRule.waitForIdle()
            scenario.onActivity { activity -> applySystemBarAppearance(activity.window, useDarkIcons = false) }
            composeRule.waitForIdle()
            captureFullScreen("02-statusbar-dark-app-light-icons.png")
        }
    }

    @Test
    fun policyDetailSectionsRemainReadableAtLargeFontScale() {
        val policy = PolicyEntity(
            id = "synthetic-policy",
            customerId = "synthetic-customer",
            insurer = "합성보험",
            productName = "합성 매우 긴 상품명 큰 글꼴 두 배에서도 줄바꿈되는지 확인하는 상품",
            paymentCycle = "MONTHLY",
            startDate = null,
            endDate = null,
            premium = 123_456,
        )
        val customer = CustomerEntity(id = "synthetic-customer", name = "합성 고객")
        val coverages = listOf(
            PolicyCoverageEntity(
                id = "synthetic-coverage",
                customerId = customer.id,
                policyId = policy.id,
                standardName = "합성 매우 긴 담보명 큰 글꼴 줄바꿈 검증",
                amount = 20_000_000,
                amountText = "2,000만원",
                rawPayloadEncrypted = "synthetic",
                updatedAt = "2026-10-25T01:02:03Z",
            )
        )

        launchActivity().use { scenario ->
            scenario.onActivity { activity ->
                activity.setContent {
                    val density = LocalDensity.current
                    CompositionLocalProvider(LocalDensity provides Density(density.density, 2.0f)) {
                        Box(Modifier.fillMaxSize().safeDrawingPadding()) {
                            PolicyDetailContent(
                                policy = policy,
                                customer = customer,
                                coverages = coverages,
                                readable = null,
                                onBack = {},
                            )
                        }
                    }
                }
            }

            composeRule.onNodeWithText("고객·상품 요약").assertIsDisplayed()
            captureFullScreen("03-policy-detail-large-font-top.png")
            composeRule.onNodeWithText("계약 정보").performScrollTo().assertIsDisplayed()
            composeRule.onNodeWithText("납입 정보").performScrollTo().assertIsDisplayed()
            composeRule.onNodeWithText("가입 담보").performScrollTo().assertIsDisplayed()
            composeRule.onNodeWithText("매월").performScrollTo().assertIsDisplayed()
            composeRule.onNodeWithText("2,000만원").performScrollTo().assertIsDisplayed()
            captureFullScreen("04-policy-detail-large-font-coverages.png")
            composeRule.onNodeWithText("없음 ~ 없음").assertDoesNotExist()
            composeRule.onNodeWithText("MONTHLY").assertDoesNotExist()
        }
    }

    private fun launchActivity(): ActivityScenario<ComponentActivity> {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val intent = Intent(Intent.ACTION_MAIN).apply {
            component = ComponentName(context.packageName, ComponentActivity::class.java.name)
        }
        return ActivityScenario.launch(intent)
    }

    private fun captureFullScreen(name: String) {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val dir = File(instrumentation.targetContext.getExternalFilesDir(null), "mobile-four-ui-feedback")
        check(dir.mkdirs() || dir.isDirectory)
        val output = File(dir, name)
        Thread.sleep(500)
        FileOutputStream(output).use { stream ->
            check(instrumentation.uiAutomation.takeScreenshot().compress(Bitmap.CompressFormat.PNG, 100, stream))
        }
        println("PHASE3_CAPTURE=${output.absolutePath}")
    }
}
