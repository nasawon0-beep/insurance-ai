package com.insuranceai.android.ui

import com.insuranceai.android.data.*
import com.insuranceai.android.data.local.*
import com.insuranceai.android.sync.*
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow

/** androidTest-only state; no database, keystore, network, worker scheduling or external intent. */
internal class SyntheticScreenModels(empty: Boolean) : CustomersModel, PoliciesModel, CoverageModel, ConsultationsModel, SyncModel {
    private val date = "2030-01-01T00:00:00Z"
    private val person = CustomerEntity(id="synthetic-a",name="합성 고객 A",phone="010-0000-0101",customerStatus="ACTIVE")
    override val customers = MutableStateFlow(if(empty) emptyList() else listOf(person))
    override val policies = MutableStateFlow(if(empty) emptyList() else listOf(PolicyEntity(id="synthetic-p",customerId=person.id,productName="합성 계약 상품",premium=0)))
    override val consultations = MutableStateFlow(if(empty) emptyList() else listOf(ConsultationEntity(id="synthetic-c",customerId=person.id,consultedAt=date,title="합성 상담",syncStatus=1)))
    override val results = MutableStateFlow(if(empty) emptyList() else listOf(CoverageAnalysisEntity("synthetic-r",person.id,"synthetic-only",date,updatedAt=date)))
    override val analyses = results
    override val coverages = MutableStateFlow(emptyList<PolicyCoverageEntity>())
    override val familyRelationships = MutableStateFlow(emptyList<FamilyRelationshipEntity>())
    override val pendingCount = MutableStateFlow(if(empty) 0 else 1)
    override val pendingItems = MutableStateFlow(emptyList<SyncOutboxEntity>())
    override val conflicts = MutableStateFlow(emptyList<SyncConflictEntity>())
    override val syncState = MutableStateFlow<SyncStateEntity?>(null)
    override val saveStatus = MutableStateFlow<String?>(null)
    override val saving = MutableStateFlow(false)
    override val status = MutableStateFlow("합성 연결 오류 · 미전송 자료 보존")
    override val pairingState = MutableStateFlow(PairingUiState())
    override val recoveryState = MutableStateFlow(RecoveryUiState(status="합성 복구 실패 · 자료 보존"))
    override val conflictResolutionState = MutableStateFlow(ConflictResolutionUiState())
    override val addressContinuityState = MutableStateFlow(AddressContinuityUiState())
    override val registered = MutableStateFlow(!empty)
    override val registrationState = MutableStateFlow(if(empty) ConnectionRegistrationState.UNREGISTERED else ConnectionRegistrationState.RECOVERY_REQUIRED)
    override val tlsBindingState = MutableStateFlow(TlsBindingState.FAILED)
    override val stagedRecoveryAvailable = MutableStateFlow(false)
    override val connectionLabel = MutableStateFlow("합성 PC")
    override fun editable(customer: CustomerEntity) = CustomerEditableFields(customer.phone,null,null)
    override fun readableFields(policy: PolicyEntity): PolicyReadableFields? = null
    override fun parsed(item: CoverageAnalysisEntity) = parseCoverageAnalysis("synthetic invalid format")
    override fun content(consultation: ConsultationEntity) = "합성 상담 본문 · 실제 자료 아님"
    private fun completed() = Job().apply { complete() }
    override fun saveAllowedEdit(customer: CustomerEntity,phone: String,address: String,memo: String,onSaved: () -> Unit): Job { onSaved();return completed() }
    override fun addConsultation(draft: ConsultationDraft,onSaved: () -> Unit): Job { onSaved();return completed() }
    override fun pair(endpoint: String,pin: String,code: String,requestAuthentication: UiAuthenticate) = completed()
    override fun recoverConnection(code: String,endpoint: String,pin: String,confirmedFromPc: Boolean,requestAuthentication: UiAuthenticate) = completed()
    override fun onPairingCodeChanged(code: String) {}
    override fun updateConnectionAddress(endpoint: String,pin: String,confirmedFromPc: Boolean,requestAuthentication: UiAuthenticate) = completed()
    override fun syncNow() = completed()
    override fun resolve(conflict: SyncConflictEntity,chooseLocal: Boolean,requestAuthentication: UiAuthenticate) = completed()
    fun ports() = ScreenModels(this,this,this,this,this)
}
