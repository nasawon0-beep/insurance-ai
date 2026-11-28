package com.insuranceai.android.ui

import android.content.ComponentName
import android.content.Intent
import android.graphics.Bitmap
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.core.view.WindowCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import com.insuranceai.android.auth.SessionManager
import com.insuranceai.android.security.AuthenticationGate
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/** Real edge-to-edge window, no test banner pushing the production header down. */
class SystemBarInsetsTest {
    @get:Rule val compose = createEmptyComposeRule()
    @Test fun brandAndFiveTabsRemainBelowSystemStatusBar() {
        val inst = InstrumentationRegistry.getInstrumentation()
        val context = inst.targetContext
        val session = SessionManager().apply { startSession("synthetic-insets-session") }
        val models = BlueCaptureModels()
        val gate = AuthenticationGate()
        ActivityScenario.launch<ComponentActivity>(Intent(Intent.ACTION_MAIN).apply {
            component = ComponentName(context.packageName, ComponentActivity::class.java.name)
        }).use { scene ->
            scene.onActivity { a ->
                WindowCompat.setDecorFitsSystemWindows(a.window, false)
                a.setContent {
                    CompositionLocalProvider(LocalScreenModels provides models.ports()) {
                        InsuranceAiApp(session, gate, true) { _, _ -> }
                    }
                }
            }
            compose.waitForIdle()
            var statusBottom = 0
            scene.onActivity { a ->
                statusBottom = ViewCompat.getRootWindowInsets(a.window.decorView)!!
                    .getInsets(WindowInsetsCompat.Type.statusBars() or WindowInsetsCompat.Type.displayCutout()).top
            }
            assertTrue("Test requires visible system bars", statusBottom > 0)
            for (tab in listOf("고객", "계약", "분석", "상담", "동기화")) {
                compose.onNode(hasText(tab) and SemanticsMatcher.expectValue(
                    androidx.compose.ui.semantics.SemanticsProperties.Role,
                    androidx.compose.ui.semantics.Role.Tab)).performClick()
                compose.waitForIdle()
                val brand = compose.onNodeWithText("모아둠").assertIsDisplayed().fetchSemanticsNode().boundsInWindow
                assertTrue("$tab brand top ${brand.top} overlaps status bar $statusBottom", brand.top >= statusBottom)
                val dir = File(context.getExternalFilesDir(null), "insets-captures").apply { mkdirs() }
                File(dir, "$tab.png").outputStream().use {
                    inst.uiAutomation.takeScreenshot().compress(Bitmap.CompressFormat.PNG, 100, it)
                }
            }
        }
    }
}
