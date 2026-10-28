package com.insuranceai.android.data

import androidx.room.withTransaction
import com.insuranceai.android.data.local.AppDatabase
import com.insuranceai.android.data.local.ConsultationEntity
import com.insuranceai.android.data.local.CoverageAnalysisEntity
import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.PolicyEntity
import com.insuranceai.android.data.local.PolicyCoverageEntity
import com.insuranceai.android.data.local.FamilyRelationshipEntity
import com.insuranceai.android.data.local.SYNC_PENDING
import com.insuranceai.android.data.local.SyncOutboxEntity
import com.insuranceai.android.data.local.nowIso
import com.insuranceai.android.security.EncryptionService
import com.insuranceai.android.sync.PostCommitSyncDispatcher
import kotlinx.coroutines.flow.Flow
import org.json.JSONObject
import java.util.UUID

data class CustomerEditableFields(val phone: String?, val address: String?, val memo: String?)

private fun jsonObject(values: Map<String, String?>): String {
    val json = JSONObject()
    values.forEach { (key, value) -> json.put(key, value ?: JSONObject.NULL) }
    return json.toString()
}

class CustomerRepository(
    private val database: AppDatabase,
    private val encryptionService: EncryptionService,
    private val postCommitSync: PostCommitSyncDispatcher? = null,
) {
    private val customerDao = database.customerDao()
    private val syncDao = database.syncDao()

    fun observeCustomers(): Flow<List<CustomerEntity>> = customerDao.observeAll()
    fun searchCustomers(query: String): Flow<List<CustomerEntity>> = customerDao.search(query)

    fun editableFields(customer: CustomerEntity) = CustomerEditableFields(
        phone = customer.phone,
        address = customer.addressEncrypted?.let(encryptionService::decrypt),
        memo = customer.memoEncrypted?.let(encryptionService::decrypt)
    )

    /** MVP allowlist: an existing customer may change only phone/address/memo. */
    suspend fun saveAllowedEdit(existing: CustomerEntity, phone: String?, address: String?, memo: String?): Boolean {
        val before = editableFields(existing)
        val now = nowIso()
        val updated = existing.copy(
            phone = phone?.ifBlank { null },
            addressEncrypted = address?.ifBlank { null }?.let(encryptionService::encrypt),
            memoEncrypted = memo?.ifBlank { null }?.let(encryptionService::encrypt),
            updatedAt = now,
            syncStatus = SYNC_PENDING
        )
        val base = jsonObject(mapOf("phone" to before.phone, "address" to before.address, "memo" to before.memo))
        val patch = jsonObject(mapOf("phone" to phone?.ifBlank { null }, "address" to address?.ifBlank { null }, "memo" to memo?.ifBlank { null }))
        val commit: suspend () -> Unit = {
            database.withTransaction {
                customerDao.update(updated)
                syncDao.enqueue(SyncOutboxEntity(entityType = "customer", entityId = existing.id, baseJson = base, patchJson = patch))
            }
        }
        val dispatcher = postCommitSync
        return if (dispatcher != null) dispatcher.run(commit).syncEnqueued else {
            commit()
            false
        }
    }
}

data class PolicyReadableFields(
    val memo: String?,
    val insuredPeriod: String?,
    val paymentPeriod: String?,
)

class PolicyRepository(
    private val database: AppDatabase,
    private val encryptionService: EncryptionService,
) {
    /** Contracts are intentionally read-only on mobile. */
    fun observePolicies(): Flow<List<PolicyEntity>> = database.policyDao().observeAll()
    fun observePolicyCoverages(): Flow<List<PolicyCoverageEntity>> = database.policyCoverageDao().observeAll()

    fun readableFields(policy: PolicyEntity) = PolicyReadableFields(
        memo = policy.memoEncrypted?.let(encryptionService::decrypt),
        insuredPeriod = policy.insuredPeriodEncrypted?.let(encryptionService::decrypt),
        paymentPeriod = policy.paymentPeriodEncrypted?.let(encryptionService::decrypt),
    )

    fun rawCoverage(item: PolicyCoverageEntity): String = encryptionService.decrypt(item.rawPayloadEncrypted)
}

class FamilyRelationshipRepository(private val database: AppDatabase) {
    fun observeRelationships(): Flow<List<FamilyRelationshipEntity>> = database.familyRelationshipDao().observeAll()
}

data class CoverageAnalysisView(val id: String, val customerId: String, val summaryJson: String, val generatedAt: String)

class CoverageAnalysisRepository(
    private val database: AppDatabase,
    private val encryptionService: EncryptionService
) {
    fun observeResults(): Flow<List<CoverageAnalysisEntity>> = database.coverageAnalysisDao().observeAll()
    fun decrypt(item: CoverageAnalysisEntity) = CoverageAnalysisView(
        item.id, item.customerId, encryptionService.decrypt(item.summaryJsonEncrypted), item.generatedAt
    )
}

class ConsultationRepository(
    private val database: AppDatabase,
    private val encryptionService: EncryptionService,
    private val postCommitSync: PostCommitSyncDispatcher? = null,
) {
    private val consultationDao = database.consultationDao()
    private val syncDao = database.syncDao()

    fun observeConsultations(): Flow<List<ConsultationEntity>> = consultationDao.observeAll()

    fun content(consultation: ConsultationEntity): String? =
        consultation.contentEncrypted?.let(encryptionService::decrypt)

    suspend fun saveConsultation(customerId: String, title: String?, channel: String?, content: String, followUpAt: String?): Boolean {
        val id = UUID.randomUUID().toString()
        val consultedAt = nowIso()
        val cleanTitle = title?.ifBlank { null }
        val cleanChannel = channel?.ifBlank { null }
        val cleanFollowUp = followUpAt?.ifBlank { null }
        val entity = ConsultationEntity(
            id = id,
            customerId = customerId,
            consultedAt = consultedAt,
            title = cleanTitle,
            channel = cleanChannel,
            contentEncrypted = encryptionService.encrypt(content),
            followUpAt = cleanFollowUp,
            syncStatus = SYNC_PENDING
        )
        val patch = jsonObject(mapOf(
            "customer_id" to customerId,
            "consulted_at" to consultedAt,
            "title" to cleanTitle,
            "channel" to cleanChannel,
            "content" to content,
            "follow_up_at" to cleanFollowUp,
            "follow_up_done_at" to null
        ))
        val commit: suspend () -> Unit = {
            database.withTransaction {
                consultationDao.insert(entity)
                syncDao.enqueue(SyncOutboxEntity(entityType = "consultation", entityId = id, baseJson = "{}", patchJson = patch))
            }
        }
        val dispatcher = postCommitSync
        return if (dispatcher != null) dispatcher.run(commit).syncEnqueued else {
            commit()
            false
        }
    }
}
