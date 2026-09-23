package com.insuranceai.android.data

import com.insuranceai.android.data.local.ConsultationDao
import com.insuranceai.android.data.local.ConsultationEntity
import com.insuranceai.android.data.local.CustomerDao
import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.PolicyDao
import com.insuranceai.android.data.local.PolicyEntity
import com.insuranceai.android.data.local.nowIso
import com.insuranceai.android.security.EncryptionService
import kotlinx.coroutines.flow.Flow

class CustomerRepository(
    private val customerDao: CustomerDao,
    private val encryptionService: EncryptionService
) {
    fun observeCustomers(): Flow<List<CustomerEntity>> = customerDao.observeAll()
    fun searchCustomers(query: String): Flow<List<CustomerEntity>> = customerDao.search(query)

    suspend fun saveCustomer(
        existing: CustomerEntity? = null,
        name: String,
        phone: String?,
        rrn: String?,
        address: String?,
        memo: String?
    ) {
        val now = nowIso()
        val entity = (existing ?: CustomerEntity(name = name, createdAt = now)).copy(
            name = name,
            phone = phone?.ifBlank { null },
            rrnEncrypted = rrn?.ifBlank { null }?.let { encryptionService.encrypt(it) },
            rrnHash = rrn?.ifBlank { null }?.let { encryptionService.hmacSha256(it) },
            addressEncrypted = address?.ifBlank { null }?.let { encryptionService.encrypt(it) },
            memoEncrypted = memo?.ifBlank { null }?.let { encryptionService.encrypt(it) },
            updatedAt = now
        )
        if (existing == null) customerDao.insert(entity) else customerDao.update(entity)
    }

    suspend fun delete(customer: CustomerEntity) = customerDao.delete(customer)
}

class PolicyRepository(private val policyDao: PolicyDao) {
    fun observePolicies(): Flow<List<PolicyEntity>> = policyDao.observeAll()
    suspend fun savePolicy(policy: PolicyEntity) = policyDao.insert(policy.copy(updatedAt = nowIso()))
    suspend fun delete(policy: PolicyEntity) = policyDao.delete(policy)
}

class ConsultationRepository(
    private val consultationDao: ConsultationDao,
    private val encryptionService: EncryptionService
) {
    fun observeConsultations(): Flow<List<ConsultationEntity>> = consultationDao.observeAll()

    suspend fun saveConsultation(customerId: String, title: String?, channel: String?, content: String, followUpAt: String?) {
        consultationDao.insert(
            ConsultationEntity(
                customerId = customerId,
                consultedAt = nowIso(),
                title = title?.ifBlank { null },
                channel = channel?.ifBlank { null },
                contentEncrypted = encryptionService.encrypt(content),
                followUpAt = followUpAt?.ifBlank { null }
            )
        )
    }

    suspend fun delete(consultation: ConsultationEntity) = consultationDao.delete(consultation)
}
