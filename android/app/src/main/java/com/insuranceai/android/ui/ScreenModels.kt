package com.insuranceai.android.ui

import androidx.compose.runtime.staticCompositionLocalOf
import com.insuranceai.android.data.*
import com.insuranceai.android.data.local.*
import com.insuranceai.android.sync.*
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.StateFlow

typealias UiAuthenticate = (() -> Unit, (String) -> Unit) -> Unit
interface CustomersModel {
    val customers: StateFlow<List<CustomerEntity>>
    val pendingCount: StateFlow<Int>
    val pendingItems: StateFlow<List<SyncOutboxEntity>>
    val conflicts: StateFlow<List<SyncConflictEntity>>
    val syncState: StateFlow<SyncStateEntity?>
    val policies: StateFlow<List<PolicyEntity>>
    val analyses: StateFlow<List<CoverageAnalysisEntity>>
    val consultations: StateFlow<List<ConsultationEntity>>
    val familyRelationships: StateFlow<List<FamilyRelationshipEntity>>
    val saveStatus: StateFlow<String?>
    val saving: StateFlow<Boolean>
    fun editable(customer: CustomerEntity): CustomerEditableFields?
    fun saveAllowedEdit(customer: CustomerEntity, phone: String, address: String, memo: String, onSaved: () -> Unit): Job
}
interface PoliciesModel {
    val policies: StateFlow<List<PolicyEntity>>
    val customers: StateFlow<List<CustomerEntity>>
    val coverages: StateFlow<List<PolicyCoverageEntity>>
    fun readableFields(policy: PolicyEntity): PolicyReadableFields?
}
interface CoverageModel {
    val results: StateFlow<List<CoverageAnalysisEntity>>
    val customers: StateFlow<List<CustomerEntity>>
    fun parsed(item: CoverageAnalysisEntity): UiCoverageAnalysis?
}
interface ConsultationsModel {
    val consultations: StateFlow<List<ConsultationEntity>>
    val customers: StateFlow<List<CustomerEntity>>
    val saveStatus: StateFlow<String?>
    val saving: StateFlow<Boolean>
    fun content(consultation: ConsultationEntity): String?
    fun addConsultation(draft: ConsultationDraft, onSaved: () -> Unit): Job
}
interface SyncModel {
    val conflicts: StateFlow<List<SyncConflictEntity>>
    val status: StateFlow<String>
    val pairingState: StateFlow<PairingUiState>
    val recoveryState: StateFlow<RecoveryUiState>
    val conflictResolutionState: StateFlow<ConflictResolutionUiState>
    val addressContinuityState: StateFlow<AddressContinuityUiState>
    val registered: StateFlow<Boolean>
    val registrationState: StateFlow<ConnectionRegistrationState>
    val tlsBindingState: StateFlow<TlsBindingState>
    val stagedRecoveryAvailable: StateFlow<Boolean>
    val connectionLabel: StateFlow<String>
    val syncState: StateFlow<SyncStateEntity?>
    val pendingCount: StateFlow<Int>
    fun pair(endpoint: String, pin: String, code: String, requestAuthentication: UiAuthenticate): Job
    fun recoverConnection(code: String, endpoint: String, pin: String, confirmedFromPc: Boolean, requestAuthentication: UiAuthenticate): Job
    fun onPairingCodeChanged(code: String)
    fun updateConnectionAddress(endpoint: String, pin: String, confirmedFromPc: Boolean, requestAuthentication: UiAuthenticate): Job
    fun syncNow(): Job
    fun resolve(conflict: SyncConflictEntity, chooseLocal: Boolean, requestAuthentication: UiAuthenticate): Job
}
/** Internal state injection for same production navigation/screens. Null always selects real ViewModels.
 * No Intent, HTTP, build flag, storage or user-facing switch can activate fixtures.
 */
internal data class ScreenModels(val customers: CustomersModel, val policies: PoliciesModel, val coverage: CoverageModel, val consultations: ConsultationsModel, val sync: SyncModel)
internal val LocalScreenModels = staticCompositionLocalOf<ScreenModels?> { null }
