package com.insuranceai.android.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.insuranceai.android.InsuranceAiApplication
import com.insuranceai.android.data.CustomerEditableFields
import com.insuranceai.android.data.local.ConsultationEntity
import com.insuranceai.android.data.local.CoverageAnalysisEntity
import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.PolicyEntity
import com.insuranceai.android.data.local.SyncConflictEntity
import com.insuranceai.android.sync.requiresUserAuthentication
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

class CustomersViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = (application as InsuranceAiApplication).container.customerRepository
    val customers: StateFlow<List<CustomerEntity>> = repository.observeCustomers()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    fun editable(customer: CustomerEntity): CustomerEditableFields = repository.editableFields(customer)

    fun saveAllowedEdit(customer: CustomerEntity, phone: String, address: String, memo: String) = viewModelScope.launch {
        repository.saveAllowedEdit(customer, phone, address, memo)
    }
}

class PoliciesViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = (application as InsuranceAiApplication).container.policyRepository
    val policies: StateFlow<List<PolicyEntity>> = repository.observePolicies()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
}

class ConsultationsViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = (application as InsuranceAiApplication).container.consultationRepository
    val consultations: StateFlow<List<ConsultationEntity>> = repository.observeConsultations()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    fun addConsultation(customerId: String, title: String, channel: String, content: String, followUpAt: String) = viewModelScope.launch {
        if (customerId.isNotBlank() && content.isNotBlank()) repository.saveConsultation(customerId, title, channel, content, followUpAt)
    }
}

class CoverageAnalysisViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = (application as InsuranceAiApplication).container.coverageAnalysisRepository
    val results: StateFlow<List<CoverageAnalysisEntity>> = repository.observeResults()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    fun summary(item: CoverageAnalysisEntity): String = repository.decrypt(item).summaryJson
}

class SyncViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = (application as InsuranceAiApplication).container.syncRepository
    val conflicts: StateFlow<List<SyncConflictEntity>> = repository.observeConflicts()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val status = MutableStateFlow("페어링 필요 또는 동기화 대기")

    init {
        viewModelScope.launch {
            repository.observeState().collect { state ->
                state?.lastError?.let { status.value = it }
            }
        }
    }

    fun pair(endpoint: String, pin: String, code: String) = viewModelScope.launch {
        status.value = "페어링 중"
        status.value = runCatching {
            repository.pair(endpoint, pin, code, "모아둠 Android")
            repository.syncOnce()
            "페어링 및 동기화 완료"
        }.getOrElse(::syncFailureStatus)
    }

    fun syncNow() = viewModelScope.launch {
        status.value = "동기화 중"
        status.value = runCatching {
            if (repository.syncOnce()) "동기화 완료" else "페어링 필요"
        }.getOrElse(::syncFailureStatus)
    }

    fun resolve(conflict: SyncConflictEntity, chooseLocal: Boolean) = viewModelScope.launch {
        status.value = "충돌 적용 중"
        status.value = runCatching {
            repository.resolveConflict(conflict, chooseLocal)
            "충돌 해결 및 동기화 완료"
        }.getOrElse(::syncFailureStatus)
    }
}

fun syncFailureStatus(error: Throwable): String =
    if (error.requiresUserAuthentication()) {
        "앱 잠금 해제 후 동기화를 다시 시도합니다"
    } else {
        "실패: ${error.message ?: error.javaClass.simpleName}"
    }
