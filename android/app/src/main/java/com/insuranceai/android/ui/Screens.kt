package com.insuranceai.android.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.insuranceai.android.data.local.ConsultationEntity
import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.PolicyEntity
import com.insuranceai.android.data.local.SyncConflictEntity

@Composable
fun LoginScreen(biometricAvailable: Boolean, onAuthenticate: (errorSink: (String) -> Unit) -> Unit) {
    var error by remember { mutableStateOf<String?>(null) }
    Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.Center) {
        Text("모아둠", fontWeight = FontWeight.Bold)
        Text("고객 자료는 기기에 암호화 저장되며, 페어링한 PC와 같은 Wi-Fi에서만 동기화합니다.")
        Spacer(Modifier.height(24.dp))
        Button(
            modifier = Modifier.fillMaxWidth(),
            enabled = biometricAvailable,
            onClick = { onAuthenticate { error = it } }
        ) { Text(if (biometricAvailable) "생체 인증으로 시작" else "생체 인증을 사용할 수 없습니다") }
        error?.let { Text("인증 실패: $it") }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CustomersScreen(viewModel: CustomersViewModel = viewModel()) {
    val customers by viewModel.customers.collectAsState()
    var editing by remember { mutableStateOf<CustomerEntity?>(null) }
    var phone by remember { mutableStateOf("") }
    var address by remember { mutableStateOf("") }
    var memo by remember { mutableStateOf("") }
    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text("모아둠 · 고객") })
        editing?.let { customer ->
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("${customer.name} 수정 · 연락처/주소/메모만 허용", fontWeight = FontWeight.Bold)
                OutlinedTextField(phone, { phone = it }, label = { Text("연락처") }, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(address, { address = it }, label = { Text("주소") }, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(memo, { memo = it }, label = { Text("메모") }, modifier = Modifier.fillMaxWidth())
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = {
                        viewModel.saveAllowedEdit(customer, phone, address, memo)
                        editing = null
                    }) { Text("오프라인 저장") }
                    TextButton(onClick = { editing = null }) { Text("취소") }
                }
            }
        }
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            items(customers, key = { it.id }) { customer ->
                CustomerCard(customer) {
                    val fields = viewModel.editable(customer)
                    phone = fields.phone.orEmpty(); address = fields.address.orEmpty(); memo = fields.memo.orEmpty()
                    editing = customer
                }
            }
        }
    }
}

@Composable
private fun CustomerCard(customer: CustomerEntity, onEdit: () -> Unit) {
    Card(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Row(Modifier.fillMaxWidth().padding(12.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            Column(Modifier.weight(1f)) {
                Text(customer.name, fontWeight = FontWeight.Bold)
                Text(customer.phone ?: "연락처 없음")
                Text("상태: ${customer.customerStatus ?: "미지정"} · 동기화: ${customer.syncStatus}")
            }
            TextButton(onClick = onEdit) { Text("수정") }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PoliciesScreen(viewModel: PoliciesViewModel = viewModel()) {
    val policies by viewModel.policies.collectAsState()
    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text("계약 · 읽기 전용") })
        Text("계약과 PC 보장분석 결과는 모바일에서 조회만 가능합니다. PDF 분석은 PC 전용입니다.", Modifier.padding(16.dp))
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            items(policies, key = { it.id }) { PolicyCard(it) }
        }
    }
}

@Composable
private fun PolicyCard(policy: PolicyEntity) {
    Card(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Column(Modifier.fillMaxWidth().padding(12.dp)) {
            Text(policy.insurer ?: "보험사 미지정", fontWeight = FontWeight.Bold)
            Text(policy.productName ?: "상품명 미지정")
            Text("월 ${policy.premium ?: 0}원 · ${policy.status}")
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CoverageAnalysisScreen(viewModel: CoverageAnalysisViewModel = viewModel()) {
    val results by viewModel.results.collectAsState()
    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text("보장분석 결과 · 읽기 전용") })
        Text("PDF 분석은 PC에서만 실행됩니다. 동기화된 결과는 오프라인에서도 조회됩니다.", Modifier.padding(16.dp))
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            items(results, key = { it.id }) { result ->
                Card(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                    Column(Modifier.padding(12.dp)) {
                        Text("고객 ${result.customerId}", fontWeight = FontWeight.Bold)
                        Text(result.generatedAt)
                        Text(viewModel.summary(result))
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConsultationsScreen(viewModel: ConsultationsViewModel = viewModel()) {
    val consultations by viewModel.consultations.collectAsState()
    var customerId by remember { mutableStateOf("") }
    var title by remember { mutableStateOf("") }
    var channel by remember { mutableStateOf("") }
    var content by remember { mutableStateOf("") }
    var followUpAt by remember { mutableStateOf("") }
    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text("상담 기록") })
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(customerId, { customerId = it }, label = { Text("고객 ID") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(title, { title = it }, label = { Text("제목") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(channel, { channel = it }, label = { Text("채널") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(content, { content = it }, label = { Text("상담 내용") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(followUpAt, { followUpAt = it }, label = { Text("후속 연락일") }, modifier = Modifier.fillMaxWidth())
            Button(onClick = {
                viewModel.addConsultation(customerId, title, channel, content, followUpAt)
                title = ""; channel = ""; content = ""; followUpAt = ""
            }) { Text("오프라인 저장") }
        }
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            items(consultations, key = { it.id }) { ConsultationCard(it) }
        }
    }
}

@Composable
private fun ConsultationCard(item: ConsultationEntity) {
    Card(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Column(Modifier.fillMaxWidth().padding(12.dp)) {
            Text(item.title ?: "상담", fontWeight = FontWeight.Bold)
            Text("${item.channel ?: "채널 미지정"} · ${item.consultedAt}")
            Text("후속 연락: ${item.followUpAt ?: "없음"}")
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SyncScreen(viewModel: SyncViewModel = viewModel()) {
    val conflicts by viewModel.conflicts.collectAsState()
    val status by viewModel.status.collectAsState()
    var endpoint by remember { mutableStateOf("") }
    var pin by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text("PC 동기화") })
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("PC에 표시된 HTTPS 주소·인증서 SHA-256·일회용 코드를 직접 확인하세요.")
            OutlinedTextField(endpoint, { endpoint = it }, label = { Text("https://PC주소:18443") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(pin, { pin = it }, label = { Text("인증서 SHA-256") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(code, { code = it }, label = { Text("일회용 페어링 코드") }, modifier = Modifier.fillMaxWidth())
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { viewModel.pair(endpoint, pin, code) }) { Text("페어링") }
                Button(onClick = viewModel::syncNow) { Text("지금 동기화") }
            }
            Text(status)
            if (conflicts.isNotEmpty()) Text("충돌 ${conflicts.size}건 · 자동 덮어쓰기하지 않습니다.", fontWeight = FontWeight.Bold)
        }
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            items(conflicts, key = { it.id }) { ConflictCard(it, viewModel::resolve) }
        }
    }
}

@Composable
private fun ConflictCard(conflict: SyncConflictEntity, resolve: (SyncConflictEntity, Boolean) -> Unit) {
    Card(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Column(Modifier.padding(12.dp)) {
            Text("${conflict.entityType} · ${conflict.kind}", fontWeight = FontWeight.Bold)
            Text("모바일 값: ${conflict.localJson}")
            Text("PC 값: ${conflict.remoteJson}")
            Row {
                TextButton(onClick = { resolve(conflict, true) }) { Text(if (conflict.localDeleted) "모바일 삭제 선택" else "모바일 값 선택") }
                TextButton(onClick = { resolve(conflict, false) }) { Text(if (conflict.remoteDeleted) "PC 삭제 선택" else "PC 값 선택") }
            }
        }
    }
}
