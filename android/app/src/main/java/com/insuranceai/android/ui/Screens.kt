package com.insuranceai.android.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.PaddingValues

import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.insuranceai.android.data.local.ConsultationEntity
import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.PolicyEntity
import com.insuranceai.android.data.PolicyReadableFields
import com.insuranceai.android.data.local.SyncConflictEntity
import com.insuranceai.android.sync.ConnectionRegistrationState
import com.insuranceai.android.sync.RecoveryUiState
import com.insuranceai.android.sync.TlsBindingState
import org.json.JSONObject
import java.text.NumberFormat
import java.util.Locale

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
fun CustomersScreen(
    selectedCustomerId: String?,
    onSelectedCustomerChange: (String?) -> Unit,
    onOpenPoliciesForCustomer: (String) -> Unit,
    onOpenSync: () -> Unit,
    viewModel: CustomersViewModel = viewModel(),
) {
    val customers by viewModel.customers.collectAsState()
    val pendingCount by viewModel.pendingCount.collectAsState()
    val pendingItems by viewModel.pendingItems.collectAsState()
    val conflicts by viewModel.conflicts.collectAsState()
    val syncState by viewModel.syncState.collectAsState()
    val saveStatus by viewModel.saveStatus.collectAsState()
    val saving by viewModel.saving.collectAsState()
    val policies by viewModel.policies.collectAsState()
    val analyses by viewModel.analyses.collectAsState()
    val consultations by viewModel.consultations.collectAsState()
    var editing by remember { mutableStateOf<CustomerEntity?>(null) }
    var query by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("") }
    var address by remember { mutableStateOf("") }
    var memo by remember { mutableStateOf("") }
    val focusManager = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    val pendingKeys = pendingItems.mapTo(mutableSetOf()) { EntityKey(it.entityType, it.entityId) }
    val conflictKeys = conflicts.mapTo(mutableSetOf()) { EntityKey(it.entityType, it.entityId) }

    val customer = editing
    if (customer != null) {
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("고객 정보 수정", fontWeight = FontWeight.Bold)
            Text(customer.name, fontWeight = FontWeight.Bold)
            Text("연락처·주소·메모는 오프라인에서도 이 기기에 먼저 저장됩니다. PC 전송 상태는 고객 목록에서 확인하세요.")
            OutlinedTextField(phone, { phone = it }, label = { Text("연락처") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(address, { address = it }, label = { Text("주소") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(
                memo,
                { memo = it },
                label = { Text("고객 메모") },
                minLines = 4,
                modifier = Modifier.fillMaxWidth(),
            )
            saveStatus?.let { Text(it) }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    modifier = Modifier.weight(1f),
                    enabled = !saving,
                    onClick = {
                        viewModel.saveAllowedEdit(customer, phone, address, memo) {
                            focusManager.clearFocus(force = true)
                            keyboard?.hide()
                            editing = null
                        }
                    },
                ) { Text("저장") }
                TextButton(
                    modifier = Modifier.weight(1f),
                    onClick = {
                        focusManager.clearFocus(force = true)
                        keyboard?.hide()
                        editing = null
                    },
                ) { Text("취소") }
            }
        }
        return
    }

    val selectedCustomer = customers.firstOrNull { it.id == selectedCustomerId }
    if (selectedCustomer != null) {
        val customerPolicies = policies.filter { it.customerId == selectedCustomer.id }
        val customerAnalyses = analyses.filter { it.customerId == selectedCustomer.id }
        val customerConsultations = consultations.filter { it.customerId == selectedCustomer.id }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            TextButton(onClick = { onSelectedCustomerChange(null) }) { Text("고객 목록으로") }
            Text(selectedCustomer.name, fontWeight = FontWeight.Bold)
            Text(selectedCustomer.phone ?: "연락처 없음")
            Text("${selectedCustomer.customerStatus ?: "상태 미지정"} · ${entitySyncLabel(entitySyncState("customer", selectedCustomer.id, selectedCustomer.syncStatus, pendingKeys, conflictKeys))}")
            TextButton(onClick = {
                val fields = viewModel.editable(selectedCustomer) ?: return@TextButton
                phone = fields.phone.orEmpty()
                address = fields.address.orEmpty()
                memo = fields.memo.orEmpty()
                editing = selectedCustomer
            }) { Text("정보 수정") }
            Text("계약 ${customerPolicies.size}건", fontWeight = FontWeight.Bold)
            customerPolicies.take(3).forEach { Text("${it.insurer ?: "보험사 미지정"} · ${it.productName ?: "상품명 미지정"} · ${policyStatusLabel(it.status)}") }
            if (customerPolicies.isEmpty()) Text("연결된 계약이 없습니다.")
            Button(
                onClick = { onOpenPoliciesForCustomer(selectedCustomer.id) },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("이 고객 계약 보기") }
            Text("보장분석 ${customerAnalyses.size}건", fontWeight = FontWeight.Bold)
            customerAnalyses.firstOrNull()?.let { Text("최근 분석 · ${formatKoreanDateTime(it.generatedAt)}") } ?: Text("연결된 보장분석이 없습니다.")
            Text("상담 ${customerConsultations.size}건", fontWeight = FontWeight.Bold)
            customerConsultations.take(3).forEach { Text("${formatKoreanDateTime(it.consultedAt)} · ${businessLabel(it.title ?: "상담")}") }
            if (customerConsultations.isEmpty()) Text("연결된 상담이 없습니다.")
        }
        return
    }

    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text("모아둠 · 고객 목록") })
        OutlinedTextField(query, { query = it }, label = { Text("이름 또는 연락처 검색") }, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp))
        PcSyncSummaryCard(
            pendingCount = pendingCount,
            syncMessage = syncState?.lastError
                ?: syncState?.lastSuccessAt?.let { "최근 동기화 성공: $it" }
                ?: "아직 동기화 기록이 없습니다",
            onOpenSync = onOpenSync,
        )
        saveStatus?.let { Text(it, Modifier.padding(horizontal = 16.dp, vertical = 4.dp)) }
        val filtered = customers.filter { query.isBlank() || it.name.contains(query, ignoreCase = true) || it.phone?.contains(query) == true }
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            if (filtered.isEmpty()) item { Text(if (query.isBlank()) "동기화된 고객이 없습니다." else "검색 결과가 없습니다.") }
            items(filtered, key = { it.id }) { item ->
                CustomerCard(item, entitySyncState("customer", item.id, item.syncStatus, pendingKeys, conflictKeys)) { onSelectedCustomerChange(item.id) }
            }
        }
    }
}

@Composable
internal fun PcSyncSummaryCard(
    pendingCount: Int,
    syncMessage: String,
    onOpenSync: () -> Unit,
) {
    Card(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(pendingSyncSummary(pendingCount), fontWeight = FontWeight.Bold)
            Text(syncMessage)
            Button(
                onClick = onOpenSync,
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
            ) {
                Text("PC 동기화 열기")
            }
        }
    }
}

fun customerSyncLabel(syncStatus: Int): String = when (syncStatus) {
    0 -> "동기화 완료"
    1 -> "PC 전송 대기"
    2 -> "동기화 충돌"
    else -> "동기화 상태 확인 필요"
}

fun pendingSyncSummary(count: Int): String =
    if (count > 0) "PC 전송 대기 ${count}건" else "전송 대기 없음"

@Composable
private fun CustomerCard(customer: CustomerEntity, syncState: UiSyncState, onOpen: () -> Unit) {
    Card(Modifier.fillMaxWidth().padding(vertical = 4.dp).clickable(onClick = onOpen)) {
        Row(Modifier.fillMaxWidth().padding(12.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(customer.name, fontWeight = FontWeight.Bold)
                Text(customer.phone ?: "연락처 없음")
                Text("고객 상태: ${customer.customerStatus ?: "미지정"}")
                Text(entitySyncLabel(syncState), fontWeight = FontWeight.Bold)
            }
            Text("보기")
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PoliciesScreen(
    selectedCustomerId: String?,
    showAllPolicies: Boolean,
    onSelectCustomer: (String) -> Unit,
    onChooseCustomer: () -> Unit,
    onShowAll: () -> Unit,
    viewModel: PoliciesViewModel = viewModel(),
) {
    val policies by viewModel.policies.collectAsState()
    val customers by viewModel.customers.collectAsState()
    val coverages by viewModel.coverages.collectAsState()
    var query by remember { mutableStateOf("") }
    var selectedPolicyId by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(selectedCustomerId, showAllPolicies) { selectedPolicyId = null }
    val browseState = PolicyBrowseState(selectedCustomerId, showAllPolicies, selectedPolicyId)
    val scopedPolicies = policiesInScope(policies, browseState)
    val selected = selectedPolicyInScope(policies, browseState)
    if (selected != null) {
        val customer = linkedCustomer(selected.customerId, customers)
        val readable = viewModel.readableFields(selected)
        PolicyDetailContent(
            policy = selected,
            customer = customer,
            coverages = coverages.filter { it.policyId == selected.id },
            readable = readable,
            onBack = { selectedPolicyId = null },
        )
        return
    }

    if (selectedCustomerId == null && !showAllPolicies) {
        val counts = policyCountsByCustomerId(policies)
        Column(Modifier.fillMaxSize()) {
            TopAppBar(title = { Text("계약 · 고객 선택") })
            Text("고객을 먼저 선택하면 해당 고객의 계약만 표시합니다.", Modifier.padding(horizontal = 16.dp, vertical = 8.dp))
            TextButton(onClick = onShowAll, modifier = Modifier.padding(horizontal = 8.dp)) { Text("전체 계약 보기 (보조)") }
            LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
                if (customers.isEmpty()) item { Text("선택할 고객이 없습니다.") }
                items(customers, key = { it.id }) { customer ->
                    Row(
                        Modifier.fillMaxWidth().clickable { onSelectCustomer(customer.id) }.padding(vertical = 12.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(customerChoice(customer).label, Modifier.weight(1f))
                        Text("${counts[customer.id] ?: 0}건", fontWeight = FontWeight.Bold)
                    }
                    HorizontalDivider()
                }
            }
        }
        return
    }

    val scopedCustomer = selectedCustomerId?.let { linkedCustomer(it, customers) }
    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text(if (showAllPolicies) "전체 계약 · 보조 보기" else "${scopedCustomer?.name ?: "선택 고객"} · 계약") })
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            TextButton(onClick = onChooseCustomer) { Text("고객 다시 선택") }
            if (!showAllPolicies) TextButton(onClick = onShowAll) { Text("전체 계약 보기") }
        }
        OutlinedTextField(query, { query = it }, label = { Text("보험사·상품 검색") }, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp))
        val filtered = scopedPolicies.filter { policy ->
            val customer = linkedCustomer(policy.customerId, customers)
            query.isBlank() || listOf(customer?.name, customer?.phone, policy.insurer, policy.productName).filterNotNull().any { it.contains(query, ignoreCase = true) }
        }
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            if (filtered.isEmpty()) item { Text(if (query.isBlank()) "선택 고객의 계약이 없습니다." else "검색 결과가 없습니다.") }
            items(filtered, key = { it.id }) { policy ->
                PolicyCard(policy, linkedCustomer(policy.customerId, customers)) { selectedPolicyId = policy.id }
            }
        }
    }
}

@Composable
private fun PolicyCard(policy: PolicyEntity, customer: CustomerEntity?, onOpen: () -> Unit) {
    Card(Modifier.fillMaxWidth().padding(vertical = 4.dp).clickable(onClick = onOpen)) {
        Column(Modifier.fillMaxWidth().padding(12.dp)) {
            Text(policy.insurer ?: "보험사 미지정", fontWeight = FontWeight.Bold)
            Text(policy.productName ?: "상품명 미지정")
            Text(customer?.let(::customerChoice)?.label ?: "연결 고객 정보 없음")
            Text("${policy.premium?.let(::formatWon) ?: "보험료 미제공"} · ${policyStatusLabel(policy.status)}")
            Text(policyClassificationLabel(policy))
        }
    }
}

private fun formatWon(value: Int): String = "${NumberFormat.getIntegerInstance(Locale.KOREA).format(value)}원"

@Composable
private fun DetailRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(label, modifier = Modifier.weight(0.38f), fontWeight = FontWeight.Bold)
        Text(value, modifier = Modifier.weight(0.62f))
    }
}

@Composable
private fun PolicySectionHeader(title: String) {
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        HorizontalDivider()
    }
}

@Composable
internal fun PolicyDetailContent(
    policy: PolicyEntity,
    customer: CustomerEntity?,
    coverages: List<com.insuranceai.android.data.local.PolicyCoverageEntity>,
    readable: PolicyReadableFields?,
    onBack: () -> Unit,
) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        TextButton(onClick = onBack) { Text("계약 목록으로") }
        Text("계약 상세", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)

        PolicySectionHeader("고객·상품 요약")
        DetailRow("고객", customer?.let(::customerChoice)?.label ?: "연결 고객 정보 미제공")
        Text(policy.productName ?: "상품명 미제공", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
        DetailRow("보험사", policy.insurer ?: "미제공")
        DetailRow("자료 분류", policyClassificationLabel(policy))

        PolicySectionHeader("계약 정보")
        DetailRow("증권번호", policy.policyNumber ?: "미제공")
        DetailRow("플랜", policy.planType?.let(::businessLabel) ?: "미제공")
        DetailRow("계약기간", policyPeriodLabel(policy.startDate, policy.endDate))
        DetailRow("상태", policyStatusLabel(policy.status))
        DetailRow("본인 계약", when (policy.isOwn) { true -> "예"; false -> "아니오"; null -> "미제공" })
        readable?.insuredPeriod?.let { DetailRow("보험기간", it) }

        PolicySectionHeader("납입 정보")
        DetailRow("보험료", policy.premium?.let(::formatWon) ?: "미제공")
        DetailRow("납입주기", paymentCycleLabel(policy.paymentCycle))
        readable?.paymentPeriod?.let { DetailRow("납입기간", it) }
        readable?.memo?.let { DetailRow("메모", it) }
        if (readable == null && listOf(policy.memoEncrypted, policy.insuredPeriodEncrypted, policy.paymentPeriodEncrypted).any { it != null }) {
            Text("암호화된 상세 항목은 앱 잠금 해제 후 다시 확인하세요.")
        }

        PolicySectionHeader("가입 담보")
        if (coverages.isEmpty()) {
            Text("동기화된 담보 원장이 없습니다. 다음 PC 동기화에서 다시 확인합니다.")
        }
        coverages.forEachIndexed { index, coverage ->
            Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(coverage.standardName ?: coverage.riderName ?: "담보명 미제공", fontWeight = FontWeight.Bold)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("가입금액", fontWeight = FontWeight.Bold)
                    Text(
                        coverage.amountText ?: coverage.amount?.let { coverageAmountValue(it).display } ?: "미제공",
                        modifier = Modifier.weight(1f),
                        textAlign = TextAlign.End,
                    )
                }
                if (coverage.startDate != null || coverage.endDate != null) {
                    DetailRow("보장기간", policyPeriodLabel(coverage.startDate, coverage.endDate))
                }
            }
            if (index != coverages.lastIndex) HorizontalDivider()
        }
        Spacer(Modifier.height(8.dp))
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CoverageAnalysisScreen(viewModel: CoverageAnalysisViewModel = viewModel()) {
    val results by viewModel.results.collectAsState()
    val customers by viewModel.customers.collectAsState()
    val authenticationResumeId = LocalAuthenticationResumeId.current
    var query by remember { mutableStateOf("") }
    var selectedId by remember { mutableStateOf<String?>(null) }
    var showRaw by remember { mutableStateOf(false) }
    var expandedRowIndex by remember { mutableStateOf<Int?>(null) }
    val selected = results.firstOrNull { it.id == selectedId }
    if (selected != null) {
        val parsed = remember(selected.id, selected.updatedAt, authenticationResumeId) { viewModel.parsed(selected) }
        if (parsed == null) {
            Text("보호된 자료를 보려면 앱 잠금을 해제해 주세요.", modifier = Modifier.padding(16.dp))
            return
        }
        val customer = linkedCustomer(selected.customerId, customers)
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            TextButton(onClick = { selectedId = null; showRaw = false }) { Text("분석 목록으로") }
            Text(customer?.name ?: "연결 고객 정보 없음", fontWeight = FontWeight.Bold)
            Text(formatKoreanDateTime(selected.generatedAt))
            Text(parsed.summary)
            if (parsed.rows.isEmpty()) Text(if (parsed.parseFailed) "분석 형식을 읽을 수 없습니다. 원본은 아래 상세에서 확인할 수 있습니다." else "지원하지 않는 분석 형식입니다. 원본은 보존되어 있습니다.")
            parsed.rows.forEachIndexed { index, row ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text(row.name, fontWeight = FontWeight.Bold)
                        row.group?.let { Text("분류 · $it") }
                        DetailRow("가입금액", row.current.display)
                        DetailRow("권장금액", row.recommended.display)
                        row.difference?.let { DetailRow(it.label, it.display) }
                        row.status?.let { DetailRow("상태", businessLabel(it)) }
                        row.fulfillment?.let { DetailRow(it.label, it.display) }
                        val hasDetails = row.sourcePage != null || row.confidence != null || row.evidence != null || row.additionalFields.isNotEmpty()
                        if (hasDetails) {
                            TextButton(onClick = { expandedRowIndex = if (expandedRowIndex == index) null else index }) {
                                Text(if (expandedRowIndex == index) "상세 닫기" else "상세 보기")
                            }
                        }
                        if (expandedRowIndex == index) {
                            row.sourcePage?.let { DetailRow(it.label, it.display) }
                            row.confidence?.let { DetailRow("분석 신뢰도", it) }
                            row.evidence?.let { DetailRow("근거 원문", it) }
                            if (row.additionalFields.isNotEmpty()) {
                                Text("원본 추가 항목 ${row.additionalFields.size}개", fontWeight = FontWeight.Bold)
                                row.additionalFields.forEach { (key, value) -> Text("$key · $value") }
                            }
                        }
                    }
                }
            }
            TextButton(onClick = { showRaw = !showRaw }) { Text(if (showRaw) "원본 닫기" else "파싱 원본 보기") }
            if (showRaw) Text(parsed.rawSource)
        }
        return
    }
    val filtered = results.filter { item ->
        val customer = linkedCustomer(item.customerId, customers)
        query.isBlank() || customer?.name?.contains(query, ignoreCase = true) == true || customer?.phone?.contains(query) == true
    }
    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text("보장분석") })
        OutlinedTextField(query, { query = it }, label = { Text("고객 이름 또는 연락처 검색") }, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp))
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp), contentPadding = PaddingValues(vertical = 8.dp)) {
            if (filtered.isEmpty()) item { Text(if (query.isBlank()) "동기화된 보장분석이 없습니다." else "검색 결과가 없습니다.") }
            items(filtered, key = { it.id }) { result ->
                val customer = linkedCustomer(result.customerId, customers)
                val parsed = remember(result.id, result.updatedAt, authenticationResumeId) { viewModel.parsed(result) }
                Card(Modifier.fillMaxWidth().padding(vertical = 4.dp).clickable { selectedId = result.id }) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        Text(customer?.name ?: "연결 고객 정보 없음", fontWeight = FontWeight.Bold)
                        Text(formatKoreanDateTime(result.generatedAt))
                        Text(
                            when {
                                parsed == null -> "보호된 자료 · 앱 잠금 해제 필요"
                                parsed.rows.isNotEmpty() -> "보장 ${parsed.rows.size}항목 · ${parsed.summary}"
                                else -> parsed.summary
                            }
                        )
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
    val customers by viewModel.customers.collectAsState()
    val saveStatus by viewModel.saveStatus.collectAsState()
    val saving by viewModel.saving.collectAsState()
    val authenticationResumeId = LocalAuthenticationResumeId.current
    var adding by remember { mutableStateOf(false) }
    var selectedConsultationId by remember { mutableStateOf<String?>(null) }
    var customerQuery by remember { mutableStateOf("") }
    var selectedCustomerId by remember { mutableStateOf<String?>(null) }
    var title by remember { mutableStateOf("") }
    var channel by remember { mutableStateOf("") }
    var content by remember { mutableStateOf("") }
    var followUpAt by remember { mutableStateOf("") }
    var listQuery by remember { mutableStateOf("") }
    var validation by remember { mutableStateOf<String?>(null) }
    val focusManager = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current

    val selectedConsultation = selectedConsultationId?.let { id -> consultations.firstOrNull { it.id == id } }
    if (selectedConsultation != null) {
        val customer = linkedCustomer(selectedConsultation.customerId, customers)
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("상담 상세", fontWeight = FontWeight.Bold)
            TextButton(onClick = { selectedConsultationId = null }) { Text("상담 목록으로") }
            DetailRow("연결 고객", customer?.let(::customerChoice)?.label ?: "연결 고객 정보 없음")
            DetailRow("업무", businessLabel(selectedConsultation.title ?: "상담"))
            DetailRow("채널", businessLabel(selectedConsultation.channel))
            DetailRow("상담일", formatKoreanDateTime(selectedConsultation.consultedAt))
            DetailRow(
                "상담 내용",
                remember(selectedConsultation.id, authenticationResumeId) { viewModel.content(selectedConsultation) }
                    ?: "보호된 자료 · 앱 잠금 해제 필요"
            )
            DetailRow("후속 연락일", formatKoreanDateTime(selectedConsultation.followUpAt))
            DetailRow("동기화 상태", consultationSyncLabel(selectedConsultation.syncStatus))
        }
        return
    }

    if (adding) {
        val selectedCustomer = selectedCustomerId?.let { id -> customers.firstOrNull { it.id == id } }
        val choices = filterCustomerChoices(customers, customerQuery).take(8)
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("상담 추가", fontWeight = FontWeight.Bold)
            TextButton(onClick = {
                focusManager.clearFocus(force = true)
                keyboard?.hide()
                adding = false
            }) { Text("상담 목록으로") }
            if (customers.isEmpty()) {
                Text("선택할 고객이 없습니다. PC 동기화 후 다시 시도해 주세요.")
            } else if (selectedCustomer != null) {
                DetailRow("선택한 고객", customerChoice(selectedCustomer).label)
                TextButton(onClick = { selectedCustomerId = null }) { Text("고객 다시 선택") }
            } else {
                OutlinedTextField(
                    customerQuery,
                    { customerQuery = it },
                    label = { Text("고객 이름 또는 연락처 검색") },
                    modifier = Modifier.fillMaxWidth(),
                )
                if (choices.isEmpty()) Text("검색 결과가 없습니다.")
                choices.forEach { choice ->
                    Card(Modifier.fillMaxWidth().clickable {
                        selectedCustomerId = choice.customerId
                        validation = null
                    }) {
                        Text(choice.label, Modifier.padding(12.dp))
                    }
                }
            }
            OutlinedTextField(title, { title = it }, label = { Text("제목 (선택)") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(channel, { channel = it }, label = { Text("채널 (선택)") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(content, { content = it }, label = { Text("상담 내용") }, minLines = 4, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(followUpAt, { followUpAt = it }, label = { Text("후속 연락일 (선택, 예: 2026-10-25)") }, modifier = Modifier.fillMaxWidth())
            validation?.let { Text(it) }
            saveStatus?.let { Text(it) }
            Button(
                enabled = !saving && selectedCustomer != null && content.isNotBlank(),
                modifier = Modifier.fillMaxWidth(),
                onClick = {
                    val draft = buildConsultationDraft(selectedCustomer, title, channel, content, followUpAt)
                    if (draft == null) {
                        validation = "고객을 선택하고 상담 내용을 입력해 주세요"
                    } else {
                        viewModel.addConsultation(draft) {
                            focusManager.clearFocus(force = true)
                            keyboard?.hide()
                            title = ""
                            channel = ""
                            content = ""
                            followUpAt = ""
                            customerQuery = ""
                            selectedCustomerId = null
                            adding = false
                        }
                    }
                },
            ) { Text(if (saving) "저장 중" else "기기에 저장") }
        }
        return
    }

    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text("상담 기록") })
        Button(onClick = { adding = true }, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)) { Text("상담 추가") }
        OutlinedTextField(listQuery, { listQuery = it }, label = { Text("고객·업무 검색") }, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp))
        saveStatus?.let { Text(it, Modifier.padding(16.dp)) }
        val filtered = consultations.filter { item ->
            val customer = linkedCustomer(item.customerId, customers)
            listQuery.isBlank() || listOf(customer?.name, customer?.phone, item.title, item.channel).filterNotNull().any { it.contains(listQuery, ignoreCase = true) }
        }
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            if (filtered.isEmpty()) item { Text(if (listQuery.isBlank()) "저장된 상담이 없습니다." else "검색 결과가 없습니다.") }
            items(filtered, key = { it.id }) { item ->
                ConsultationCard(item, linkedCustomer(item.customerId, customers)) { selectedConsultationId = item.id }
            }
        }
    }
}

@Composable
private fun ConsultationCard(item: ConsultationEntity, customer: CustomerEntity?, onOpen: () -> Unit) {
    Card(Modifier.fillMaxWidth().padding(vertical = 4.dp).clickable(onClick = onOpen)) {
        Column(Modifier.fillMaxWidth().padding(12.dp)) {
            Text(businessLabel(item.title ?: "상담"), fontWeight = FontWeight.Bold)
            Text(customer?.let(::customerChoice)?.label ?: "연결 고객 정보 없음")
            Text("${businessLabel(item.channel)} · ${formatKoreanDateTime(item.consultedAt)}")
            Text("후속 연락: ${formatKoreanDateTime(item.followUpAt)}")
            Text(consultationSyncLabel(item.syncStatus))

        }
    }
}

fun consultationSyncLabel(syncStatus: Int): String = customerSyncLabel(syncStatus)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SyncScreen(
    onAuthenticate: (onSuccess: () -> Unit, onError: (String) -> Unit) -> Unit,
    viewModel: SyncViewModel = viewModel(),
) {
    val conflicts by viewModel.conflicts.collectAsState()
    val status by viewModel.status.collectAsState()
    val pairingState by viewModel.pairingState.collectAsState()
    val recoveryState by viewModel.recoveryState.collectAsState()
    val stagedRecoveryAvailable by viewModel.stagedRecoveryAvailable.collectAsState()
    val registrationState by viewModel.registrationState.collectAsState()
    val tlsBindingState by viewModel.tlsBindingState.collectAsState()
    val resolutionState by viewModel.conflictResolutionState.collectAsState()
    val addressState by viewModel.addressContinuityState.collectAsState()
    val registered by viewModel.registered.collectAsState()
    val connectionLabel by viewModel.connectionLabel.collectAsState()
    val syncState by viewModel.syncState.collectAsState()
    val deleteConfirmation = remember { MissingCustomerDeleteConfirmationCoordinator() }
    val deleteTarget by deleteConfirmation.target.collectAsState()
    var endpoint by remember { mutableStateOf("") }
    var pin by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var recoveryCode by remember { mutableStateOf("") }
    var movedEndpoint by remember { mutableStateOf("") }
    var movedPin by remember { mutableStateOf("") }
    var movedPinConfirmed by remember { mutableStateOf(false) }
    LaunchedEffect(pairingState.codeConsumed) {
        if (pairingState.codeConsumed) code = ""
    }
    LaunchedEffect(recoveryState.codeConsumed) {
        if (recoveryState.codeConsumed) recoveryCode = ""
    }
    LaunchedEffect(deleteTarget, conflicts) {
        val target = deleteTarget
        if (target != null && conflicts.none { it == target }) deleteConfirmation.dismiss()
    }
    Column(Modifier.fillMaxSize()) {
        TopAppBar(title = { Text("PC 동기화") })
        LazyColumn(
            Modifier.fillMaxSize().imePadding(),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item {
                if (registered) Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(if (registrationState == ConnectionRegistrationState.RECOVERY_REQUIRED) "기존 연결 복구 필요" else "등록된 PC", fontWeight = FontWeight.Bold)
                        Text(connectionLabel)
                        Text(when (tlsBindingState) {
                            TlsBindingState.READY -> "자동 주소 전환 준비 완료"
                            TlsBindingState.PENDING -> "자동 주소 전환 준비 중 · 다른 활성 기기도 업데이트가 필요할 수 있습니다"
                            TlsBindingState.FAILED -> "자동 주소 전환 준비 실패 · 연결은 유지되며 다시 동기화해 주세요"
                            TlsBindingState.NOT_REQUIRED -> "자동 주소 전환 정보 미등록"
                        })
                        Text("최근 동기화 · ${formatKoreanDateTime(syncState?.lastSuccessAt)}")
                        if (registrationState == ConnectionRegistrationState.RECOVERY_REQUIRED) {
                            Text("PC에서 연결이 해제되었거나 인증 정보를 다시 확인해야 합니다. 기존 저장 자료와 미전송 자료는 그대로 유지됩니다.")
                        } else {
                            Text("등록 정보와 인증서 확인을 재사용하므로 연결할 때마다 새 코드를 받을 필요가 없습니다.")
                        }
                    }
                } else Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("처음 PC 등록", fontWeight = FontWeight.Bold)
                        Text("PC에 표시된 HTTPS 주소·인증서 SHA-256·일회용 코드를 직접 확인하세요.")
                        OutlinedTextField(endpoint, { endpoint = it }, enabled = !pairingState.inFlight, label = { Text("https://PC주소:18443") }, modifier = Modifier.fillMaxWidth())
                        OutlinedTextField(pin, { pin = it }, enabled = !pairingState.inFlight, label = { Text("인증서 SHA-256") }, modifier = Modifier.fillMaxWidth())
                        OutlinedTextField(code, {
                            code = it
                            viewModel.onPairingCodeChanged(it)
                        }, enabled = !pairingState.inFlight, visualTransformation = PasswordVisualTransformation(), label = { Text("일회용 페어링 코드") }, modifier = Modifier.fillMaxWidth())
                        Button(
                            modifier = Modifier.fillMaxWidth(),
                            enabled = !pairingState.inFlight && !pairingState.requiresNewCode && code.matches(Regex("\\d{6}")),
                            onClick = { viewModel.pair(endpoint, pin, code, onAuthenticate) },
                        ) { Text(if (pairingState.inFlight) "처리 중" else "PC와 페어링") }
                    }
                }
            }
            item {
                if (registered && registrationState == ConnectionRegistrationState.RECOVERY_REQUIRED) {
                    RecoveryConnectionCard(
                        code = recoveryCode,
                        onCodeChange = { recoveryCode = it },
                        movedEndpoint = movedEndpoint,
                        onMovedEndpointChange = { movedEndpoint = it },
                        movedPin = movedPin,
                        onMovedPinChange = { movedPin = it },
                        movedPinConfirmed = movedPinConfirmed,
                        onMovedPinConfirmedChange = { movedPinConfirmed = it },
                        state = recoveryState,
                        stagedRecoveryAvailable = stagedRecoveryAvailable,
                        onRecover = {
                            viewModel.recoverConnection(
                                recoveryCode,
                                movedEndpoint,
                                movedPin,
                                movedPinConfirmed,
                                onAuthenticate,
                            )
                        },
                    )
                }
            }
            item {
                if (registered && registrationState == ConnectionRegistrationState.ACTIVE) Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("PC 주소 변경 복구", fontWeight = FontWeight.Bold)
                        Text("처음 등록을 다시 하지 않습니다. PC 화면의 새 주소와 인증서 확인값을 직접 대조한 경우에만 기존 연결과 미해결 항목을 확인합니다.")
                        OutlinedTextField(
                            movedEndpoint,
                            { movedEndpoint = it },
                            enabled = !addressState.inFlight,
                            label = { Text("새 https://PC주소:포트") },
                            modifier = Modifier.fillMaxWidth(),
                        )
                        OutlinedTextField(
                            movedPin,
                            { movedPin = it },
                            enabled = !addressState.inFlight,
                            label = { Text("PC 화면의 새 인증서 SHA-256") },
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Row {
                            Checkbox(
                                checked = movedPinConfirmed,
                                onCheckedChange = { movedPinConfirmed = it },
                                enabled = !addressState.inFlight,
                            )
                            Text("PC 화면의 새 주소와 인증서 SHA-256이 정확히 일치합니다")
                        }
                        Text(addressState.status)
                        Button(
                            enabled = !addressState.inFlight && movedPinConfirmed,
                            onClick = {
                                viewModel.updateConnectionAddress(movedEndpoint, movedPin, movedPinConfirmed, onAuthenticate)
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text(if (addressState.inFlight) "동일 PC 확인 중" else "기존 연결 주소 갱신") }
                    }
                }
            }
            item {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("동기화 상태", fontWeight = FontWeight.Bold)
                        Text(status)
                        Text("자동 동기화는 네트워크 연결 후 OS가 실행하며, 실행 시각은 보장되지 않습니다.")
                        Button(enabled = registered && registrationState == ConnectionRegistrationState.ACTIVE && !pairingState.inFlight, onClick = viewModel::syncNow, modifier = Modifier.fillMaxWidth()) { Text("지금 동기화") }
                        if (!registered) Text("PC를 처음 등록하면 이후에는 새 코드 없이 동기화합니다.")
                        if (conflicts.isNotEmpty()) Text("충돌 ${conflicts.size}건 · 자동 덮어쓰기하지 않습니다.", fontWeight = FontWeight.Bold)
                    }
                }
            }
            items(conflicts, key = { it.id }) { conflict ->
                ConflictCard(
                    conflict = conflict,
                    inFlight = conflict.id in resolutionState.inFlightIds,
                    status = resolutionState.statusByConflict[conflict.id],
                    resolve = { chooseLocal -> viewModel.resolve(conflict, chooseLocal, onAuthenticate) },
                    requestDeleteConfirmation = { remoteMissing ->
                        deleteConfirmation.request(conflict, remoteMissing)
                    },
                )
            }
        }
    }
    deleteTarget?.let {
        AlertDialog(
            onDismissRequest = deleteConfirmation::dismiss,
            title = { Text("모바일 고객 삭제 확인") },
            text = {
                Text("PC에 이 고객이 없습니다. 계속하면 모바일 고객과 연결된 보험계약·상담·보장분석이 함께 삭제될 수 있으며 복구할 수 없습니다.")
            },
            dismissButton = {
                TextButton(onClick = deleteConfirmation::dismiss) { Text("취소") }
            },
            confirmButton = {
                TextButton(onClick = {
                    deleteConfirmation.confirm(conflicts) { target ->
                        viewModel.resolve(target, chooseLocal = false, onAuthenticate)
                    }
                }) { Text("삭제 확인") }
            },
        )
    }
}

@Composable
internal fun RecoveryConnectionCard(
    code: String,
    onCodeChange: (String) -> Unit,
    movedEndpoint: String = "",
    onMovedEndpointChange: (String) -> Unit = {},
    movedPin: String = "",
    onMovedPinChange: (String) -> Unit = {},
    movedPinConfirmed: Boolean = false,
    onMovedPinConfirmedChange: (Boolean) -> Unit = {},
    state: RecoveryUiState,
    stagedRecoveryAvailable: Boolean = false,
    onRecover: () -> Unit,
) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("기존 연결 복구", fontWeight = FontWeight.Bold)
            Text(
                if (stagedRecoveryAvailable && code.isBlank()) {
                    "이전 복구 요청의 PC 승인을 확인할 수 있습니다. 확인 실패 시 PC에서 새 복구 코드를 생성하세요. 기존 미전송 자료는 삭제되지 않습니다."
                } else {
                    "PC에서 이 기기를 선택해 만든 복구 코드를 입력하세요. 기존 미전송 자료는 삭제되지 않습니다."
                }
            )
            OutlinedTextField(
                code,
                onCodeChange,
                enabled = !state.inFlight,
                visualTransformation = PasswordVisualTransformation(),
                label = { Text("복구 코드") },
                modifier = Modifier.fillMaxWidth(),
            )
            Text("PC 주소도 바뀌었다면 PC 화면의 새 주소와 인증서 SHA-256을 직접 대조해 입력하세요. 확인 전에는 코드가 전송되지 않습니다.")
            OutlinedTextField(
                movedEndpoint,
                onMovedEndpointChange,
                enabled = !state.inFlight,
                label = { Text("새 https://PC주소:포트 (선택)") },
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                movedPin,
                onMovedPinChange,
                enabled = !state.inFlight,
                label = { Text("새 인증서 SHA-256 (선택)") },
                modifier = Modifier.fillMaxWidth(),
            )
            Row {
                Checkbox(
                    checked = movedPinConfirmed,
                    onCheckedChange = onMovedPinConfirmedChange,
                    enabled = !state.inFlight,
                )
                Text("PC 화면의 새 주소와 인증서 SHA-256이 정확히 일치합니다")
            }
            Text(state.status)
            Button(
                enabled = !state.inFlight &&
                    (code.matches(Regex("\\d{6}")) || (code.isBlank() && stagedRecoveryAvailable)) &&
                    ((movedEndpoint.isBlank() && movedPin.isBlank()) ||
                        (movedEndpoint.isNotBlank() && movedPin.isNotBlank() && movedPinConfirmed)),
                onClick = onRecover,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    when {
                        state.inFlight -> "복구 중"
                        code.isBlank() && stagedRecoveryAvailable -> "복구 확인 다시 시도"
                        else -> "기존 연결 복구"
                    }
                )
            }
        }
    }
}

@Composable
private fun ConflictCard(
    conflict: SyncConflictEntity,
    inFlight: Boolean,
    status: String?,
    resolve: (Boolean) -> Unit,
    requestDeleteConfirmation: (Boolean) -> Unit,
) {
    val card = customerConflictCardUi(
        base = JSONObject(conflict.baseJson).toValueMap(),
        local = JSONObject(conflict.localJson).toValueMap(),
        remote = JSONObject(conflict.remoteJson).toValueMap(),
    )
    Card(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            card.customerName?.let { Text("고객: $it", fontWeight = FontWeight.Bold) }
            if (card.fields.isEmpty()) {
                Text("표시할 충돌 항목을 확인할 수 없습니다.")
            }
            card.fields.forEach { field ->
                Text("항목: ${field.label}", fontWeight = FontWeight.Bold)
                Text("모바일 값: ${field.mobileValue ?: "없음"}")
                Text("PC 값: ${field.pcValue ?: "없음"}")
            }
            if (card.remoteMissing) {
                Text("PC에 없는 고객입니다. PC 없음 선택 시 모바일 고객과 연결된 보험계약·상담·보장분석이 함께 삭제될 수 있으며 복구할 수 없습니다.")
            }
            status?.let { Text(it) }
            Row {
                TextButton(enabled = !inFlight && !card.remoteMissing, onClick = { resolve(true) }) { Text(if (conflict.localDeleted) "모바일 삭제 선택" else "모바일 값 선택") }
                TextButton(
                    enabled = !inFlight,
                    onClick = {
                        if (card.remoteMissing) requestDeleteConfirmation(true) else resolve(false)
                    },
                ) { Text(if (conflict.remoteDeleted || card.remoteMissing) "PC 없음 선택" else "PC 값 선택") }
            }
        }
    }
}

private fun JSONObject.toValueMap(): Map<String, Any?> = keys().asSequence().associateWith { key ->
    if (isNull(key)) null else get(key)
}
