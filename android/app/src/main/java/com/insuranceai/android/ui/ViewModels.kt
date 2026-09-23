package com.insuranceai.android.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.insuranceai.android.InsuranceAiApplication
import com.insuranceai.android.data.local.ConsultationEntity
import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.PolicyEntity
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

class CustomersViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = (application as InsuranceAiApplication).container.customerRepository
    val customers: StateFlow<List<CustomerEntity>> = repository.observeCustomers()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    fun addCustomer(name: String, phone: String, rrn: String, address: String, memo: String) = viewModelScope.launch {
        if (name.isNotBlank()) repository.saveCustomer(name = name, phone = phone, rrn = rrn, address = address, memo = memo)
    }

    fun deleteCustomer(customer: CustomerEntity) = viewModelScope.launch { repository.delete(customer) }
}

class PoliciesViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = (application as InsuranceAiApplication).container.policyRepository
    val policies: StateFlow<List<PolicyEntity>> = repository.observePolicies()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    fun addPolicy(customerId: String, insurer: String, productName: String, premium: String) = viewModelScope.launch {
        if (customerId.isNotBlank()) repository.savePolicy(
            PolicyEntity(
                customerId = customerId,
                insurer = insurer.ifBlank { null },
                productName = productName.ifBlank { null },
                premium = premium.toIntOrNull()
            )
        )
    }

    fun deletePolicy(policy: PolicyEntity) = viewModelScope.launch { repository.delete(policy) }
}

class ConsultationsViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = (application as InsuranceAiApplication).container.consultationRepository
    val consultations: StateFlow<List<ConsultationEntity>> = repository.observeConsultations()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    fun addConsultation(customerId: String, title: String, channel: String, content: String, followUpAt: String) = viewModelScope.launch {
        if (customerId.isNotBlank() && content.isNotBlank()) repository.saveConsultation(customerId, title, channel, content, followUpAt)
    }

    fun deleteConsultation(consultation: ConsultationEntity) = viewModelScope.launch { repository.delete(consultation) }
}
