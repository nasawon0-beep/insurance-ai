package com.insuranceai.android.ui

import android.content.ComponentName
import android.content.Intent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.unit.sp
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class BlueThemeTest {
 @get:Rule val compose=createEmptyComposeRule()
 @Test fun approvedLightPaletteAndReadableTypeAreProductionTheme(){
  val context=InstrumentationRegistry.getInstrumentation().targetContext
  var primary:Color?=null;var ink:Color?=null;var muted:Color?=null;var chip:Color?=null;var body:androidx.compose.ui.unit.TextUnit?=null
  ActivityScenario.launch<ComponentActivity>(Intent(Intent.ACTION_MAIN).apply{component=ComponentName(context.packageName,ComponentActivity::class.java.name)}).use { scene ->
   scene.onActivity { a ->a.setContent{MoaTheme(false){primary=MaterialTheme.colorScheme.primary;ink=MaterialTheme.colorScheme.onSurface;muted=MaterialTheme.colorScheme.onSurfaceVariant;chip=MaterialTheme.colorScheme.secondaryContainer;body=MaterialTheme.typography.bodyMedium.fontSize;Text("테마 검증")}}}
   compose.waitForIdle()
   assertEquals(Color(0xFF0055FF),primary)
   assertEquals(Color(0xFF08163C),ink)
   assertEquals(Color(0xFF6E7B96),muted)
   assertEquals(13.sp,body)
   assertEquals("Filter chips must not retain default Material purple",Color(0xFFEDF4FF),chip)
  }
 }
 @Test fun approvedDarkPaletteIsNotALightSurface(){
  val context=InstrumentationRegistry.getInstrumentation().targetContext
  var primary:Color?=null;var surface:Color?=null;var ink:Color?=null
  ActivityScenario.launch<ComponentActivity>(Intent(Intent.ACTION_MAIN).apply{component=ComponentName(context.packageName,ComponentActivity::class.java.name)}).use { scene ->
   scene.onActivity { a ->a.setContent{MoaTheme(true){primary=MaterialTheme.colorScheme.primary;surface=MaterialTheme.colorScheme.surface;ink=MaterialTheme.colorScheme.onSurface;Text("다크 테마 검증")}}}
   compose.waitForIdle()
   assertEquals(Color(0xFF6F9EFF),primary)
   assertEquals(Color(0xFF111E34),surface)
   assertEquals(Color(0xFFE5EDFF),ink)
  }
 }
}
