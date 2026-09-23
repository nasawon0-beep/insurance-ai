package com.insuranceai.android.data.local

import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey
import java.time.Instant
import java.util.UUID

const val SYNC_SYNCED = 0
const val SYNC_PENDING = 1
const val SYNC_CONFLICT = 2

private fun newId(): String = UUID.randomUUID().toString()
fun nowIso(): String = Instant.now().toString()

@Entity(tableName = "customers")
data class CustomerEntity(
    @PrimaryKey val id: String = newId(),
    val name: String,
    val phone: String? = null,
    val birthDate: String? = null,
    val gender: String? = null,
    val email: String? = null,
    val addressEncrypted: String? = null,
    val occupation: String? = null,
    val tagsEncrypted: String? = null,
    val memoEncrypted: String? = null,
    val rrnEncrypted: String? = null,
    val rrnHash: String? = null,
    val customerStatus: String? = null,
    val importBatchId: String? = null,
    val createdAt: String = nowIso(),
    val updatedAt: String = nowIso(),
    val syncStatus: Int = SYNC_PENDING
)

@Entity(
    tableName = "policies",
    foreignKeys = [ForeignKey(
        entity = CustomerEntity::class,
        parentColumns = ["id"],
        childColumns = ["customerId"],
        onDelete = ForeignKey.CASCADE
    )],
    indices = [Index("customerId")]
)
data class PolicyEntity(
    @PrimaryKey val id: String = newId(),
    val customerId: String,
    val insurer: String? = null,
    val productName: String? = null,
    val policyNumber: String? = null,
    val planType: String? = null,
    val premium: Int? = null,
    val paymentCycle: String? = null,
    val startDate: String? = null,
    val endDate: String? = null,
    val status: String = "ACTIVE",
    val memoEncrypted: String? = null,
    val documentId: String? = null,
    val insuredPeriodEncrypted: String? = null,
    val paymentPeriodEncrypted: String? = null,
    val isOwn: Boolean? = null,
    val createdAt: String = nowIso(),
    val updatedAt: String = nowIso(),
    val syncStatus: Int = SYNC_PENDING
)

@Entity(
    tableName = "consultations",
    foreignKeys = [ForeignKey(
        entity = CustomerEntity::class,
        parentColumns = ["id"],
        childColumns = ["customerId"],
        onDelete = ForeignKey.CASCADE
    )],
    indices = [Index("customerId"), Index("followUpAt")]
)
data class ConsultationEntity(
    @PrimaryKey val id: String = newId(),
    val customerId: String,
    val consultedAt: String,
    val channel: String? = null,
    val title: String? = null,
    val contentEncrypted: String? = null,
    val transcriptEncrypted: String? = null,
    val followUpAt: String? = null,
    val followUpDoneAt: String? = null,
    val createdAt: String = nowIso(),
    val updatedAt: String = nowIso(),
    val syncStatus: Int = SYNC_PENDING
)
