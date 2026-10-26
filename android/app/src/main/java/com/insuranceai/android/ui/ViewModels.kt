package com.insuranceai.android.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.insuranceai.android.InsuranceAiApplication
import com.insuranceai.android.data.CustomerEditableFields
import com.insuranceai.android.data.PolicyReadableFields
import com.insuranceai.android.data.local.ConsultationEntity
import com.insuranceai.android.data.local.CoverageAnalysisEntity
import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.PolicyEntity
import com.insuranceai.android.data.local.PolicyCoverageEntity
import com.insuranceai.android.data.local.SyncConflictEntity
import com.insuranceai.android.data.local.SyncStateEntity
import com.insuranceai.android.security.AuthenticationRequiredException
import com.insuranceai.android.sync.requiresUserAuthentication
import com.insuranceai.android.sync.ConflictResolutionCoordinator
import com.insuranceai.android.sync.AddressContinuityCoordinator
import com.insuranceai.android.sync.AddressContinuityRequest
import com.insuranceai.android.sync.PairingCoordinator
import com.insuranceai.android.sync.PairingRequest
import com.insuranceai.android.sync.RecoveryCoordinator
import com.insuranceai.android.sync.RecoveryRequest
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

class CustomersViewModel(application: Application) : AndroidViewModel(application) {
    private val container = (application as InsuranceAiApplication).container
    private val repository = container.customerRepository
    private val syncRepository = container.syncRepository
    private val policyRepository = container.policyRepository
    private val analysisRepository = container.coverageAnalysisRepository
    private val consultationRepository = container.consultationRepository
    val customers: StateFlow<List<CustomerEntity>> = repository.observeCustomers()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val pendingCount: StateFlow<Int> = syncRepository.observePendingCount()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), 0)
    val pendingItems = syncRepository.observePendingItems()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val conflicts = syncRepository.observeConflicts()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val syncState: StateFlow<SyncStateEntity?> = syncRepository.observeState()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), null)
    val policies = policyRepository.observePolicies()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val analyses = analysisRepository.observeResults()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val consultations = consultationRepository.observeConsultations()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val saveStatus = MutableStateFlow<String?>(null)
    val saving = MutableStateFlow(false)

    fun editable(customer: CustomerEntity): CustomerEditableFields? =
        container.authenticationGate.readOrNull { repository.editableFields(customer) }

    fun saveAllowedEdit(
        customer: CustomerEntity,
        phone: String,
        address: String,
        memo: String,
        onSaved: () -> Unit,
    ) = viewModelScope.launch {
        if (saving.value) return@launch
        saving.value = true
        saveStatus.value = "기기에 저장 중"
        try {
            val scheduled = repository.saveAllowedEdit(customer, phone, address, memo)
            saveStatus.value = if (scheduled) {
                "기기에 저장됨 · PC 전송 대기 작업을 예약했습니다"
            } else {
                "기기에 저장됨 · 자동 전송 예약 실패, 주기 동기화 또는 다시 잠금 해제 후 재시도됩니다"
            }
            onSaved()
        } catch (_: AuthenticationRequiredException) {
            saveStatus.value = "재인증 후 입력 내용을 유지한 채 저장을 다시 시도합니다"
        } catch (error: Exception) {
            saveStatus.value = "기기 저장 실패 (${error.javaClass.simpleName})"
        } finally {
            saving.value = false
        }
    }
}

class PoliciesViewModel(application: Application) : AndroidViewModel(application) {
    private val container = (application as InsuranceAiApplication).container
    private val repository = container.policyRepository
    private val customerRepository = container.customerRepository
    val policies: StateFlow<List<PolicyEntity>> = repository.observePolicies()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val customers: StateFlow<List<CustomerEntity>> = customerRepository.observeCustomers()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val coverages: StateFlow<List<PolicyCoverageEntity>> = repository.observePolicyCoverages()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    fun readableFields(policy: PolicyEntity): PolicyReadableFields? =
        container.authenticationGate.readOrNull { repository.readableFields(policy) }
}

class ConsultationsViewModel(application: Application) : AndroidViewModel(application) {
    private val container = (application as InsuranceAiApplication).container
    private val repository = container.consultationRepository
    private val customerRepository = container.customerRepository
    val consultations: StateFlow<List<ConsultationEntity>> = repository.observeConsultations()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val customers: StateFlow<List<CustomerEntity>> = customerRepository.observeCustomers()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val saveStatus = MutableStateFlow<String?>(null)
    val saving = MutableStateFlow(false)

    fun content(consultation: ConsultationEntity): String? =
        container.authenticationGate.readOrNull {
            repository.content(consultation) ?: "상담 내용 없음"
        }

    fun addConsultation(draft: ConsultationDraft, onSaved: () -> Unit) = viewModelScope.launch {
        if (saving.value) return@launch
        if (customers.value.none { it.id == draft.customerId }) {
            saveStatus.value = "목록에서 고객을 다시 선택해 주세요"
            return@launch
        }
        saving.value = true
        saveStatus.value = "기기에 상담 저장 중"
        try {
            val scheduled = repository.saveConsultation(
                draft.customerId,
                draft.title,
                draft.channel,
                draft.content,
                draft.followUpAt,
            )
            saveStatus.value = if (scheduled) {
                "상담 저장 완료 · PC 전송 대기"
            } else {
                "상담은 저장됨 · 자동 전송 예약은 주기 동기화에서 재시도됩니다"
            }
            onSaved()
        } catch (_: AuthenticationRequiredException) {
            saveStatus.value = "재인증 후 입력 내용을 유지한 채 상담 저장을 다시 시도합니다"
        } catch (error: Exception) {
            saveStatus.value = "상담 저장 실패 (${error.javaClass.simpleName})"
        } finally {
            saving.value = false
        }
    }
}

class CoverageAnalysisViewModel(application: Application) : AndroidViewModel(application) {
    private val container = (application as InsuranceAiApplication).container
    private val repository = container.coverageAnalysisRepository
    private val customerRepository = container.customerRepository
    val results: StateFlow<List<CoverageAnalysisEntity>> = repository.observeResults()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val customers: StateFlow<List<CustomerEntity>> = customerRepository.observeCustomers()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    fun parsed(item: CoverageAnalysisEntity): UiCoverageAnalysis? =
        container.authenticationGate.readOrNull {
            parseCoverageAnalysis(repository.decrypt(item).summaryJson)
        }
}

class SyncViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = (application as InsuranceAiApplication).container.syncRepository
    private val pairingCoordinator = PairingCoordinator(repository)
    private val recoveryCoordinator = RecoveryCoordinator(repository)
    private val conflictResolutionCoordinator = ConflictResolutionCoordinator(repository)
    private val addressContinuityCoordinator = AddressContinuityCoordinator(repository)
    val conflicts: StateFlow<List<SyncConflictEntity>> = repository.observeConflicts()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val status = MutableStateFlow("페어링 필요 또는 동기화 대기")
    val pairingState = pairingCoordinator.state
    val recoveryState = recoveryCoordinator.state
    val conflictResolutionState = conflictResolutionCoordinator.state
    val addressContinuityState = addressContinuityCoordinator.state
    val registered = MutableStateFlow(repository.isConnectionConfigured())
    val registrationState = MutableStateFlow(repository.connectionRegistrationState())
    val stagedRecoveryAvailable = MutableStateFlow(repository.hasStagedRecovery())
    val connectionLabel = MutableStateFlow(repository.connectionLabel())
    val syncState: StateFlow<SyncStateEntity?> = repository.observeState()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), null)

    init {
        viewModelScope.launch {
            syncState.collect { state ->
                registrationState.value = repository.connectionRegistrationState()
                if (!pairingState.value.codeConsumed && state != null) status.value = syncStatusFromState(state)
            }
        }
        viewModelScope.launch {
            pairingState.collect { state ->
                status.value = state.status
            }
        }
    }

    fun pair(
        endpoint: String,
        pin: String,
        code: String,
        requestAuthentication: (onSuccess: () -> Unit, onError: (String) -> Unit) -> Unit,
    ) = viewModelScope.launch {
        pairingCoordinator.pair(PairingRequest(endpoint, pin, code, "모아둠 Android")) {
            suspendCancellableCoroutine { continuation ->
                requestAuthentication(
                    { if (continuation.isActive) continuation.resume(true) },
                    { if (continuation.isActive) continuation.resume(false) },
                )
            }
        }
        registered.value = repository.isConnectionConfigured()
        registrationState.value = repository.connectionRegistrationState()
        stagedRecoveryAvailable.value = repository.hasStagedRecovery()
        connectionLabel.value = repository.connectionLabel()
    }

    fun recoverConnection(
        code: String,
        requestAuthentication: (onSuccess: () -> Unit, onError: (String) -> Unit) -> Unit,
    ) = viewModelScope.launch {
        recoveryCoordinator.recover(RecoveryRequest(code)) {
            suspendCancellableCoroutine { continuation ->
                requestAuthentication(
                    { if (continuation.isActive) continuation.resume(true) },
                    { if (continuation.isActive) continuation.resume(false) },
                )
            }
        }
        registered.value = repository.isConnectionConfigured()
        registrationState.value = repository.connectionRegistrationState()
        stagedRecoveryAvailable.value = repository.hasStagedRecovery()
        connectionLabel.value = repository.connectionLabel()
    }

    fun onPairingCodeChanged(code: String) = pairingCoordinator.onCodeChanged(code)

    fun updateConnectionAddress(
        endpoint: String,
        pin: String,
        confirmedFromPc: Boolean,
        requestAuthentication: (onSuccess: () -> Unit, onError: (String) -> Unit) -> Unit,
    ) = viewModelScope.launch {
        addressContinuityCoordinator.update(
            AddressContinuityRequest(endpoint, pin, confirmedFromPc)
        ) {
            suspendCancellableCoroutine { continuation ->
                requestAuthentication(
                    { if (continuation.isActive) continuation.resume(true) },
                    { if (continuation.isActive) continuation.resume(false) },
                )
            }
        }
    }

    fun syncNow() = viewModelScope.launch {
        status.value = "동기화 중"
        try {
            status.value = runCatching {
                if (repository.syncOnce()) "동기화 완료" else "페어링 필요"
            }.getOrElse(::syncFailureStatus)
        } finally {
            registrationState.value = repository.connectionRegistrationState()
        }
    }

    fun resolve(
        conflict: SyncConflictEntity,
        chooseLocal: Boolean,
        requestAuthentication: (onSuccess: () -> Unit, onError: (String) -> Unit) -> Unit,
    ) = viewModelScope.launch {
        conflictResolutionCoordinator.resolve(conflict, chooseLocal) {
            suspendCancellableCoroutine { continuation ->
                requestAuthentication(
                    { if (continuation.isActive) continuation.resume(true) },
                    { if (continuation.isActive) continuation.resume(false) },
                )
            }
        }
    }
}

fun syncFailureStatus(error: Throwable): String =
    if (error.requiresUserAuthentication()) {
        "앱 잠금 해제 후 동기화를 다시 시도합니다"
    } else if (error is com.insuranceai.android.sync.LanSyncHttpException && error.statusCode == 401) {
        "PC에서 연결이 해제되었거나 인증 정보를 다시 확인해야 합니다 · 기존 자료는 그대로입니다"
    } else if (error is java.net.SocketTimeoutException) {
        "PC 응답 시간 초과 · 네트워크 연결을 확인해 주세요"
    } else {
        "실패: ${error.message ?: error.javaClass.simpleName}"
    }

fun syncStatusFromState(state: SyncStateEntity): String = when {
    state.lastError != null -> state.lastError
    state.lastSuccessAt != null -> "동기화 완료"
    else -> "페어링 필요 또는 동기화 대기"
}
