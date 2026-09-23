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
import androidx.compose.material3.CardDefaults
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
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.insuranceai.android.data.local.ConsultationEntity
import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.PolicyEntity

@Composable
fun LoginScreen(
    biometricAvailable: Boolean,
    onAuthenticate: (errorSink: (String) -> Unit) -> Unit
) {
    var error by remember { mutableStateOf<String?>(null) }
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center
    ) {
        Text("Insurance AI", fontWeight = FontWeight.Bold)
        Text("로컬 전용 MVP: 생체 인증 후 고객 정보를 안전하게 관리합니다.")
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
    var name by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("") }
    var rrn by remember { mutableStateOf("") }
    var address by remember { mutableStateOf("") }
    var memo by remember { mutableStateOf("") }

    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text("고객 관리") })
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(name, { name = it }, label = { Text("이름") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(phone, { phone = it }, label = { Text("전화번호") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(
                rrn,
                { rrn = it },
                label = { Text("주민번호") },
                visualTransformation = PasswordVisualTransformation(),
                modifier = Modifier.fillMaxWidth()
            )
            OutlinedTextField(address, { address = it }, label = { Text("주소(암호화)") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(memo, { memo = it }, label = { Text("메모(암호화)") }, modifier = Modifier.fillMaxWidth())
            Button(onClick = {
                viewModel.addCustomer(name, phone, rrn, address, memo)
                name = ""; phone = ""; rrn = ""; address = ""; memo = ""
            }) { Text("고객 추가") }
        }
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            items(customers, key = { it.id }) { customer ->
                CustomerCard(customer = customer, onDelete = { viewModel.deleteCustomer(customer) })
            }
        }
    }
}

@Composable
private fun CustomerCard(customer: CustomerEntity, onDelete: () -> Unit) {
    Card(Modifier.fillMaxWidth().padding(vertical = 4.dp), elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)) {
        Row(Modifier.fillMaxWidth().padding(12.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            Column(Modifier.weight(1f)) {
                Text(customer.name, fontWeight = FontWeight.Bold)
                Text(customer.phone ?: "전화번호 없음")
                Text("상태: ${customer.customerStatus ?: "미지정"} / 동기화: ${customer.syncStatus}")
            }
            TextButton(onClick = onDelete) { Text("삭제") }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PoliciesScreen(viewModel: PoliciesViewModel = viewModel()) {
    val policies by viewModel.policies.collectAsState()
    var customerId by remember { mutableStateOf("") }
    var insurer by remember { mutableStateOf("") }
    var productName by remember { mutableStateOf("") }
    var premium by remember { mutableStateOf("") }

    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text("보험 계약") })
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(customerId, { customerId = it }, label = { Text("고객 ID") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(insurer, { insurer = it }, label = { Text("보험사") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(productName, { productName = it }, label = { Text("상품명") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(premium, { premium = it }, label = { Text("월 보험료") }, modifier = Modifier.fillMaxWidth())
            Button(onClick = {
                viewModel.addPolicy(customerId, insurer, productName, premium)
                insurer = ""; productName = ""; premium = ""
            }) { Text("계약 추가") }
        }
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            items(policies, key = { it.id }) { policy ->
                PolicyCard(policy = policy, onDelete = { viewModel.deletePolicy(policy) })
            }
        }
    }
}

@Composable
private fun PolicyCard(policy: PolicyEntity, onDelete: () -> Unit) {
    Card(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Row(Modifier.fillMaxWidth().padding(12.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            Column(Modifier.weight(1f)) {
                Text(policy.insurer ?: "보험사 미지정", fontWeight = FontWeight.Bold)
                Text(policy.productName ?: "상품명 미지정")
                Text("월 ${policy.premium ?: 0}원 / ${policy.status}")
            }
            TextButton(onClick = onDelete) { Text("삭제") }
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
        TopAppBar(title = { Text("상담 이력") })
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(customerId, { customerId = it }, label = { Text("고객 ID") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(title, { title = it }, label = { Text("제목") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(channel, { channel = it }, label = { Text("채널") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(content, { content = it }, label = { Text("상담 내용(암호화)") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(followUpAt, { followUpAt = it }, label = { Text("후속 연락일(YYYY-MM-DD)") }, modifier = Modifier.fillMaxWidth())
            Button(onClick = {
                viewModel.addConsultation(customerId, title, channel, content, followUpAt)
                title = ""; channel = ""; content = ""; followUpAt = ""
            }) { Text("상담 추가") }
        }
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            items(consultations, key = { it.id }) { consultation ->
                ConsultationCard(consultation = consultation, onDelete = { viewModel.deleteConsultation(consultation) })
            }
        }
    }
}

@Composable
private fun ConsultationCard(consultation: ConsultationEntity, onDelete: () -> Unit) {
    Card(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Row(Modifier.fillMaxWidth().padding(12.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            Column(Modifier.weight(1f)) {
                Text(consultation.title ?: "상담", fontWeight = FontWeight.Bold)
                Text("${consultation.channel ?: "채널 미지정"} / ${consultation.consultedAt}")
                Text("후속 연락: ${consultation.followUpAt ?: "없음"}")
            }
            TextButton(onClick = onDelete) { Text("삭제") }
        }
    }
}
