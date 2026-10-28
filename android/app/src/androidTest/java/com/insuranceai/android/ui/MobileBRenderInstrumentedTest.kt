package com.insuranceai.android.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class MobileBRenderInstrumentedTest {
    @get:Rule val compose = createComposeRule()

    @Test
    fun renderSyntheticMobileBSurfaceForHostCapture() {
        val arguments = InstrumentationRegistry.getArguments()
        assumeTrue(arguments.getString("renderCapture") == "true")
        val screen = arguments.getString("captureScreen") ?: "detail"
        compose.setContent { MobileBPreview(screen) }
        compose.waitForIdle()
        Thread.sleep(30_000)
    }
}

@Composable
private fun MobileBPreview(screen: String) {
    val selectedTab = when (screen) {
        "policies" -> 1
        "analysis" -> 2
        "sync" -> 4
        else -> 0
    }
    MaterialTheme(
        colorScheme = lightColorScheme(
            primary = Color(0xFF304F76), onPrimary = Color.White,
            primaryContainer = Color(0xFFE7EDF4), background = Color(0xFFF4F5F6),
            surface = Color.White, onSurface = Color(0xFF242B33),
        )
    ) {
        Scaffold(
            bottomBar = {
                NavigationBar {
                    listOf("고객", "계약", "분석", "상담", "동기화").forEachIndexed { index, label ->
                        NavigationBarItem(selected = index == selectedTab, onClick = {}, icon = { Text("·") }, label = { Text(label, fontSize = 11.sp) })
                    }
                }
            }
        ) { padding ->
            Column(
                Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 10.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                when (screen) {
                    "customers" -> CustomersPreview()
                    "policies" -> PoliciesPreview()
                    "analysis" -> AnalysisPreview()
                    "sync" -> SyncPreview()
                    else -> CustomerDetailPreview()
                }
            }
        }
    }
}

@Composable
private fun CustomersPreview() {
    Text("모아둠 · 고객", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
    Text("고객 이름 또는 연락처 검색", color = Color(0xFF58636F), modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable {})
    Text("전체     활성     기타", color = MaterialTheme.colorScheme.primary, modifier = Modifier.heightIn(min = 44.dp))
    Text("사무실 PC · 동기화 완료 · 전송 대기 없음", style = MaterialTheme.typography.bodySmall)
    HorizontalDivider()
    PreviewRow("합성 가족 A", "계약 2건 · 보장 점검 예정", "활성")
    PreviewRow("합성 가족 B", "계약 1건 · 최근 상담 기록", "활성")
    PreviewRow("합성 자녀 C", "계약 1건 · 자녀 보장 확인", "활성")
    PreviewRow("합성 고객 D", "계약 없음 · 첫 상담 예정", "기타")
    Text("합성 고객 자료 · 실제 고객 자료 아님", style = MaterialTheme.typography.bodySmall)
}

@Composable
private fun CustomerDetailPreview() {
    Text("‹ 고객 목록", color = MaterialTheme.colorScheme.primary, modifier = Modifier.heightIn(min = 44.dp).clickable {})
    Text("합성 가족 A", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
    Text("010-0000-0101 · 활성 · 동기화 완료")
    HorizontalDivider()
    Text("가족", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
    PreviewRow("합성 가족 B · 010-0000-0102", "배우자", "이동")
    PreviewRow("합성 자녀 C · 2018-03-04", "자녀", "이동")
    Text("가족 관계는 PC에서 편집할 수 있습니다.", style = MaterialTheme.typography.bodySmall)
    HorizontalDivider()
    Text("합성 가족 A 고객 자료", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
    PreviewRow("계약", "2건 · 월 117,600원", "보기")
    PreviewRow("보장분석", "최근 분석 1건", "보기")
    PreviewRow("상담 기록", "최근 상담 2026년 10월 27일", "보기")
    Text("가족 고객의 계약·분석·상담은 합치지 않습니다.", style = MaterialTheme.typography.bodySmall)
}

@Composable
private fun PoliciesPreview() {
    Text("계약", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
    Text("선택 고객 · 합성 가족 A", color = MaterialTheme.colorScheme.primary)
    Text("확정 계약 2건 · 월 보험료", style = MaterialTheme.typography.bodySmall)
    Text("117,600원", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
    Text("금액 큰 순 · 참고 자료 제외", style = MaterialTheme.typography.bodySmall)
    HorizontalDivider()
    PreviewRow("모아생명 · 무배당 오래 함께하는 종합건강보험", "월 86,400원 · 갱신형", "담보")
    PreviewRow("안심손해 · 가족생활보장보험", "월 31,200원 · 비갱신형", "담보")
    Text("참고 자료", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
    PreviewRow("추가 보장 제안서", "미확정 · 보험료 합계 제외", "보기")
    Text("합성 계약 자료 · 실제 계약 아님", style = MaterialTheme.typography.bodySmall)
}

@Composable
private fun AnalysisPreview() {
    Text("보장분석", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
    Text("선택 고객 · 합성 가족 A", color = MaterialTheme.colorScheme.primary)
    Text("가입금액과 권장 예시", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
    PreviewRow("일반암 진단비", "가입 3,000만원 · 권장 5,000만원", "근거")
    PreviewRow("뇌혈관 진단비", "가입 확인 필요 · 권장 2,000만원", "사유")
    PreviewRow("상해 수술비", "가입 0원 · 권장 100만원", "근거")
    Text("확인 필요 금액은 0원으로 계산하지 않습니다.", style = MaterialTheme.typography.bodySmall)
    Text("권장금액은 화면 설명용 합성 예시이며 보험 권고가 아닙니다.", style = MaterialTheme.typography.bodySmall)
}

@Composable
private fun SyncPreview() {
    Text("PC 동기화", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
    Text("사무실 PC", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
    PreviewRow("연결 상태", "연결됨", "정상")
    PreviewRow("최근 동기화", "오늘 09:32", "")
    PreviewRow("전송 대기", "상담 기록 1건", "")
    Text("지금 동기화", color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.Bold, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable {}.padding(12.dp))
    Text("같은 Wi-Fi에서 PC와 자료를 주고받습니다.", style = MaterialTheme.typography.bodySmall)
    HorizontalDivider()
    Text("내용이 다른 항목 1건", fontWeight = FontWeight.Bold)
    Text("합성 가족 A 상담 기록이 PC와 다릅니다. 확인하기 전에는 덮어쓰지 않습니다.")
    PreviewRow("차이 확인하기", "선택 전에는 어느 쪽도 덮어쓰지 않음", "이동")
    PreviewRow("고급 설정", "주소·인증서·복구·연결 해제", "열기")
}

@Composable
private fun PreviewRow(primary: String, secondary: String, action: String) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 52.dp).clickable {}.padding(vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Column(Modifier.weight(1f)) {
            Text(primary, fontWeight = FontWeight.SemiBold)
            Text(secondary, style = MaterialTheme.typography.bodySmall)
        }
        if (action.isNotBlank()) Text(action, color = MaterialTheme.colorScheme.primary)
    }
    HorizontalDivider()
}
