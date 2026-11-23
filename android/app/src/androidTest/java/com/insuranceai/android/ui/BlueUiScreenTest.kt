package com.insuranceai.android.ui

import android.content.ComponentName
import android.content.Intent
import android.content.res.Configuration
import android.graphics.Bitmap
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.Text
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import com.insuranceai.android.auth.SessionManager
import com.insuranceai.android.security.AuthenticationGate
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import java.io.File

/** Production AppNavigation + production screens, memory ports only in androidTest. */
class BlueUiScreenTest {
 @get:Rule val compose=createEmptyComposeRule()
 private fun launch(models:BlueCaptureModels,dark:Boolean=false,fontScale:Float=1f):ActivityScenario<ComponentActivity>{
  val context=InstrumentationRegistry.getInstrumentation().targetContext
  val scenario=ActivityScenario.launch<ComponentActivity>(Intent(Intent.ACTION_MAIN).apply{component=ComponentName(context.packageName,ComponentActivity::class.java.name)})
  val session=SessionManager().apply{startSession("synthetic-ui-test-session")};val gate=AuthenticationGate()
  scenario.onActivity { a ->
   val density=a.resources.displayMetrics.widthPixels.toFloat()/410f
   val config=Configuration(a.resources.configuration).apply{uiMode=(uiMode and Configuration.UI_MODE_NIGHT_MASK.inv()) or if(dark) Configuration.UI_MODE_NIGHT_YES else Configuration.UI_MODE_NIGHT_NO}
   a.setContent {CompositionLocalProvider(LocalConfiguration provides config,LocalDensity provides Density(density,fontScale),LocalScreenModels provides models.ports(),LocalCustomerContactLauncher provides { _:Intent ->Unit }) {
    MoaTheme(dark){Column(Modifier.fillMaxSize()){
     Text("UI 검증 · 가상 데이터",Modifier.fillMaxWidth().background(Color(0xFFFFF4CC)).padding(8.dp),color=Color(0xFF513D00),fontSize=12.sp)
     Box(Modifier.weight(1f)){InsuranceAiApp(session,gate,true){_,_->}}
    }}
   }}
  };compose.waitForIdle();return scenario
 }
 private fun tap(text:String){compose.onNodeWithText(text).performClick();compose.waitForIdle()}
 private fun nav(text:String){compose.onNode(hasText(text) and SemanticsMatcher.expectValue(androidx.compose.ui.semantics.SemanticsProperties.Role,androidx.compose.ui.semantics.Role.Tab)).performClick();compose.waitForIdle()}
 private fun scroll(text:String){compose.onNodeWithText(text).performScrollTo();compose.waitForIdle()}
 private fun shot(id:String,expected:String){
  compose.waitForIdle();compose.onNodeWithText("UI 검증 · 가상 데이터").assertIsDisplayed();compose.onAllNodesWithText(expected).onFirst().assertIsDisplayed()
  val inst=InstrumentationRegistry.getInstrumentation();val dir=File(inst.targetContext.getExternalFilesDir(null),"blue-ui-captures");check(dir.mkdirs()||dir.isDirectory)
  Thread.sleep(250);File(dir,"$id.png").outputStream().use{check(inst.uiAutomation.takeScreenshot().compress(Bitmap.CompressFormat.PNG,100,it))}
  File(dir,"$id.txt").writeText(compose.onRoot().printToString(maxDepth=80))
 }
 @Test fun customerCardsShowTheirOwnCountsAndShortcutsKeepNavigation(){
  val models=BlueCaptureModels()
  launch(models).use {
   compose.onAllNodesWithText("계약 1건 · 보장분석 1건 · 상담 1건").assertCountEquals(3)
   shot("light-customers","고객 목록")
   tap(models.customers.value.first().name)
   val contract=compose.onNodeWithText("계약 1건").fetchSemanticsNode().boundsInRoot
   val analysis=compose.onNodeWithText("보장분석 1건").fetchSemanticsNode().boundsInRoot
   assertEquals("Summary shortcuts share one row at normal font scale",contract.top,analysis.top,2f)
   shot("light-customer-detail","고객 상세")
   tap("계약 1건");shot("light-customer-policies","가상 보장 예시 1")
   tap("가상 보장 예시 1");shot("light-contract-detail","계약 상세")
   scroll("3만원");shot("light-contract-coverages","3만원")
   nav("분석");shot("light-analysis-list","보장분석")
   tap(models.customers.value.first().name);shot("light-analysis-detail","분석 전체 4항목")
   scroll("파싱 원본 보기");shot("light-analysis-bottom","질병 입원일당")
   scroll("암 (1)");tap("암 (1)");shot("light-analysis-category","선택: 암 · 표시 1 / 전체 4항목")
  }
 }
 @Test fun darkEmptyLongManyAndLargeFontUseTheSameProductionScreens(){
  for(variant in listOf("dark","empty","long","many","large-font","failed")){
   val models=BlueCaptureModels(variant)
   launch(models,variant=="dark",if(variant=="large-font")1.3f else 1f).use {
    shot("$variant-customers","고객 목록")
    if(variant=="empty"){
     compose.onNodeWithText("동기화된 고객이 없습니다.").assertIsDisplayed()
     nav("계약");tap("전체 계약 보기 (보조)");shot("empty-policies","전체 계약 · 보조 보기")
     nav("분석");shot("empty-analysis","동기화된 보장분석이 없습니다.")
    }else{
     if(variant=="many"){
      compose.onAllNodes(hasScrollAction()).onFirst().performScrollToNode(hasText(models.customers.value.last().name))
      shot("many-customers-bottom",models.customers.value.last().name)
      compose.onAllNodes(hasScrollAction()).onFirst().performScrollToIndex(0)
     }
     tap(models.customers.value.first().name);shot("$variant-customer-detail","고객 상세")
     val count=models.policies.value.count{it.customerId==models.customers.value.first().id}
     tap("계약 ${count}건");shot("$variant-contract-list",models.policies.value.first().productName!!)
     if(variant=="many"){
      compose.onAllNodes(hasScrollAction()).onFirst().performScrollToNode(hasText(models.policies.value[count-1].productName!!))
      shot("many-contract-list-bottom",models.policies.value[count-1].productName!!)
     }
     nav("분석");tap(models.customers.value.first().name);shot("$variant-analysis-detail",if(variant=="failed") "분석 전체 0항목" else "분석 전체 4항목")
     scroll("파싱 원본 보기");shot("$variant-analysis-bottom",if(variant=="failed") "분석 형식을 읽을 수 없습니다. 원본은 아래 상세에서 확인할 수 있습니다." else "질병 입원일당")
     if(variant=="failed") {tap("파싱 원본 보기");scroll("합성 형식 실패 입력 · 가상 데이터");shot("failed-analysis-source","합성 형식 실패 입력 · 가상 데이터")}
    }
   }
  }
 }
}
