package com.insuranceai.android.ui

import android.content.ComponentName
import android.content.Intent
import android.graphics.Bitmap
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.Surface
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import com.insuranceai.android.data.CustomerEditableFields
import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.PolicyEntity
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.Parameterized
import java.io.File

/** Calls actual production composables, not the historical MobileBPreview copy.
 * Synthetic state only; no repository, real phone, central Auth or sync transport.
 * Requires separately approved test-device installation; compile is NOT render success.
 */
@RunWith(Parameterized::class)
class ProductionBRenderTest(private val width: Int, private val fontScale: Float, private val dark: Boolean) {
    companion object {
        @JvmStatic @Parameterized.Parameters(name = "{0}dp-font{1}-dark{2}")
        fun matrix() = listOf(360,390,430).flatMap { width ->
            listOf(1f,2f).flatMap { font -> listOf(false,true).map { dark -> arrayOf<Any>(width,font,dark) } }
        }
    }
    @get:Rule val compose = createEmptyComposeRule()
    private val customer = CustomerEntity(id="synthetic-a",name="합성 고객 A",phone="010-0000-0101",customerStatus="ACTIVE")

    @Test fun productionCustomerListDetailEditAndContactAreIndependent() {
        val intents = mutableListOf<Intent>()
        var navigations = 0
        launch().use { scenario ->
            scenario.onActivity { activity ->
                val density = activity.resources.displayMetrics.widthPixels.toFloat()/width
                activity.setContent {
                    CompositionLocalProvider(LocalDensity provides Density(density,fontScale),
                        LocalCustomerContactLauncher provides { intent -> intents.add(intent); Unit }) {
                        MoaTheme(dark) { Surface {
                            var selected by remember { mutableStateOf<String?>(null) }
                            CustomersContent(selected, { selected=it;navigations++ }, {}, {}, {}, {},
                                listOf(customer),0,emptyList(),emptyList(),null,null,false,
                                emptyList(),emptyList(),emptyList(),emptyList(),
                                { CustomerEditableFields(phone=customer.phone,address=null,memo=null) },
                                { _,_,_,_,saved -> saved() })
                        } }
                    }
                }
            }
            compose.onNodeWithText("전화하기").assertIsDisplayed().assertHeightIsAtLeast(44.dp).performClick()
            compose.onNodeWithText("문자하기").assertIsDisplayed().assertHeightIsAtLeast(44.dp).performClick()
            compose.runOnIdle {
                assertEquals(0,navigations)
                assertEquals(listOf(Intent.ACTION_DIAL,Intent.ACTION_SENDTO),intents.map { it.action })
                assertEquals(listOf("tel","smsto"),intents.map { it.data!!.scheme })
            }
            capture("customers")
            compose.onNodeWithText(customer.name).performClick()
            compose.onNodeWithText("고객 상세").assertIsDisplayed()
            compose.onNodeWithText("전화하기").performScrollTo().assertIsDisplayed().assertHeightIsAtLeast(44.dp)
            capture("detail")
            compose.onNodeWithText("계약 0건").performScrollTo().assertIsDisplayed()
            compose.onNodeWithText("등록된 계약 없음").assertExists()
            compose.onNodeWithText("가족 관계는 PC에서 편집할 수 있습니다.").performScrollTo().assertIsDisplayed()
            capture("detail-family")
            compose.onNodeWithText("정보 수정").performScrollTo().performClick()
            compose.onNodeWithText("고객 정보 수정").assertIsDisplayed()
            capture("customer-edit")
        }
    }

    @Test fun productionPolicyDetailPreservesMissingAndZero() {
        launch().use { scenario ->
            scenario.onActivity { activity ->
                val density=activity.resources.displayMetrics.widthPixels.toFloat()/width
                activity.setContent {
                    CompositionLocalProvider(LocalDensity provides Density(density,fontScale)) {
                        MoaTheme(dark) { Surface {
                            PolicyDetailContent(PolicyEntity(id="synthetic-p",customerId=customer.id,
                                insurer="합성보험",productName="합성 긴 상품 이름 계약 화면 검수",premium=0),
                                customer,emptyList(),null,{})
                        } }
                    }
                }
            }
            compose.onNodeWithText("계약 상세").assertIsDisplayed()
            capture("policy-detail")
            compose.onNodeWithText("0원").performScrollTo().assertIsDisplayed()
            capture("policy-zero-premium")
        }
    }

    @Test fun fullProductionNavigationPopulatedAndRecoveryStates() = navigationScenario(false)
    @Test fun fullProductionNavigationEmptyStates() = navigationScenario(true)

    private fun navigationScenario(empty: Boolean) {
        val models = SyntheticScreenModels(empty)
        val gate = com.insuranceai.android.security.AuthenticationGate()
        val session = com.insuranceai.android.auth.SessionManager().apply { startSession("synthetic-session") }
        launch().use { scenario ->
            scenario.onActivity { activity ->
                val density = activity.resources.displayMetrics.widthPixels.toFloat()/width
                val configuration = android.content.res.Configuration(activity.resources.configuration).apply {
                    uiMode = (uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK.inv()) or
                        if(dark) android.content.res.Configuration.UI_MODE_NIGHT_YES else android.content.res.Configuration.UI_MODE_NIGHT_NO
                }
                activity.setContent {
                    CompositionLocalProvider(LocalDensity provides Density(density,fontScale),
                        androidx.compose.ui.platform.LocalConfiguration provides configuration,
                        LocalScreenModels provides models.ports(),
                        LocalCustomerContactLauncher provides { _: Intent -> Unit }) {
                        InsuranceAiApp(session,gate,true) { success,_ -> session.startSession("synthetic-session");gate.onAuthenticationSucceeded();success() }
                    }
                }
            }
            for (tab in listOf("고객","계약","분석","상담","동기화")) {
                compose.onNodeWithText(tab).performClick()
                compose.waitForIdle()
                capture("navigation-${if(empty) "empty" else "populated"}-$tab")
                if (tab == "계약") {
                    compose.onNodeWithText("전체 계약 보기 (보조)").performClick()
                    capture("navigation-contract-list-$empty")
                    if (!empty) {
                        compose.onNodeWithText("합성 계약 상품").performClick()
                        compose.onNodeWithText("계약 상세").assertIsDisplayed()
                        capture("navigation-contract-detail")
                    }
                }
                if (tab == "분석" && !empty) {
                    compose.onNodeWithText("합성 고객 A").performClick()
                    capture("navigation-analysis-parse-error")
                }
                if (tab == "상담") {
                    compose.onNodeWithText("상담 추가").performClick()
                    capture("navigation-consultation-compose-$empty")
                    compose.onNodeWithText("상담 목록으로").performClick()
                    if(!empty) { compose.onNodeWithText("합성 상담").performClick();capture("navigation-consultation-detail") }
                }
                if(tab == "동기화") {
                    compose.onAllNodes(hasScrollAction()).onFirst().performScrollToNode(hasText("동기화 상태"))
                    capture("navigation-sync-error-$empty")
                }
            }
            compose.runOnIdle { runCatching { gate.runSensitive<Unit> { throw com.insuranceai.android.security.AuthenticationRequiredException() } } }
            compose.waitForIdle()
            capture("navigation-lock-$empty")
        }
    }

    @Test fun coverageCategoriesPreserveValuesAndResetOnAnalysisChange() {
        val a = com.insuranceai.android.data.local.CoverageAnalysisEntity("category-a", customer.id, "synthetic", "2026-01-01", updatedAt="1")
        val b = a.copy(id="category-b", customerId="synthetic-b")
        val model = object : CoverageModel {
            override val results = kotlinx.coroutines.flow.MutableStateFlow(listOf(a,b))
            override val customers = kotlinx.coroutines.flow.MutableStateFlow(listOf(customer, customer.copy(id=b.customerId,name="합성 고객 B")))
            override fun parsed(item: com.insuranceai.android.data.local.CoverageAnalysisEntity) = parseCoverageAnalysis("""{"coverage_items":[{"coverage_name":"합성 암 항목","coverage_group":"암","current_amount":0,"recommended_amount":null},{"coverage_name":"합성 미분류","coverage_group":"새분류"}]}""")
        }
        launch().use { scenario ->
            scenario.onActivity { activity ->
                val density = activity.resources.displayMetrics.widthPixels.toFloat()/width
                activity.setContent { CompositionLocalProvider(LocalDensity provides Density(density,fontScale)) {
                    MoaTheme(dark) { Surface { CoverageAnalysisScreen(viewModel=model) } }
                } }
            }
            compose.onNodeWithText("합성 고객 A").performClick()
            compose.onNodeWithText("암 (1)").performScrollTo().assertHeightIsAtLeast(44.dp).performClick().assertIsSelected()
            compose.onNodeWithText("선택: 암 · 표시 1 / 전체 2항목").performScrollTo().assertIsDisplayed()
            compose.onNodeWithText("합성 미분류").assertDoesNotExist()
            compose.onNodeWithText("0원").performScrollTo().assertIsDisplayed()
            compose.onNodeWithText("값 없음").performScrollTo().assertIsDisplayed()
            capture("coverage-category-cancer")
            compose.onNodeWithText("치매 (0)").performScrollTo().performClick()
            compose.onNodeWithText("선택한 카테고리에 보장 항목이 없습니다. 전체를 선택해 주세요.").performScrollTo().assertIsDisplayed()
            compose.onNodeWithText("기타 (1)").performScrollTo().assertHeightIsAtLeast(44.dp).performClick()
            compose.onNodeWithText("합성 미분류").performScrollTo().assertIsDisplayed()
            capture("coverage-category-other")
            compose.onNodeWithText("전체 (2)").performScrollTo().performClick()
            compose.onNodeWithText("합성 암 항목").assertExists()
            compose.onNodeWithText("암 (1)").performScrollTo().performClick()
            compose.onNodeWithText("분석 목록으로").performScrollTo().performClick()
            compose.onNodeWithText("합성 고객 B").performClick()
            compose.onNodeWithText("전체 (2)").assertIsSelected()
            capture("coverage-category-reset")
        }
    }

    private fun launch(): ActivityScenario<ComponentActivity> {
        val context=InstrumentationRegistry.getInstrumentation().targetContext
        return ActivityScenario.launch(Intent(Intent.ACTION_MAIN).apply {
            component=ComponentName(context.packageName,ComponentActivity::class.java.name)
        })
    }
    private fun capture(screen: String) {
        compose.waitForIdle()
        val instrumentation=InstrumentationRegistry.getInstrumentation()
        val directory=File(instrumentation.targetContext.getExternalFilesDir(null),"integrated-b-production")
        check(directory.mkdirs() || directory.isDirectory)
        val output=File(directory,"${screen}-${width}-${fontScale}-${if(dark) "dark" else "light"}.png")
        output.outputStream().use { check(instrumentation.uiAutomation.takeScreenshot().compress(Bitmap.CompressFormat.PNG,100,it)) }
        println("PRODUCTION_CAPTURE=${output.absolutePath}")
    }
}
