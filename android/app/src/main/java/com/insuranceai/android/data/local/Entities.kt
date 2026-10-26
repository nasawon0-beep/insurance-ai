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
    val documentStatus: String? = null,
    val sourceDocumentType: String? = null,
    val insuredPeriodEncrypted: String? = null,
    val paymentPeriodEncrypted: String? = null,
    val isOwn: Boolean? = null,
    val createdAt: String = nowIso(),
    val updatedAt: String = nowIso(),
    val syncStatus: Int = SYNC_PENDING
)

@Entity(
    tableName = "policy_coverages",
    foreignKeys = [ForeignKey(
        entity = CustomerEntity::class,
        parentColumns = ["id"],
        childColumns = ["customerId"],
        onDelete = ForeignKey.CASCADE
    )],
    indices = [Index("customerId"), Index("policyId")]
)
data class PolicyCoverageEntity(
    @PrimaryKey val id: String,
    val customerId: String,
    val policyId: String? = null,
    val insurer: String? = null,
    val productName: String? = null,
    val riderName: String? = null,
    val standardName: String? = null,
    val amount: Long? = null,
    val amountText: String? = null,
    val amountStatus: String? = null,
    val documentStatus: String? = null,
    val startDate: String? = null,
    val endDate: String? = null,
    val rawPayloadEncrypted: String,
    val updatedAt: String,
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

@Entity(
    tableName = "coverage_analysis_results",
    foreignKeys = [ForeignKey(
        entity = CustomerEntity::class,
        parentColumns = ["id"],
        childColumns = ["customerId"],
        onDelete = ForeignKey.CASCADE
    )],
    indices = [Index("customerId")]
)
data class CoverageAnalysisEntity(
    @PrimaryKey val id: String,
    val customerId: String,
    val summaryJsonEncrypted: String,
    val generatedAt: String,
    val sourceHash: String? = null,
    val updatedAt: String
)

@Entity(tableName = "sync_outbox", indices = [Index(value = ["operationId"], unique = true)])
data class SyncOutboxEntity(
    @PrimaryKey(autoGenerate = true) val localId: Long = 0,
    val operationId: String = newId(),
    val entityType: String,
    val entityId: String,
    val baseJson: String,
    val patchJson: String,
    val deleted: Boolean = false,
    val createdAt: String = nowIso(),
    val attemptCount: Int = 0
)

@Entity(tableName = "sync_conflicts")
data class SyncConflictEntity(
    @PrimaryKey val id: String,
    val entityType: String,
    val entityId: String,
    val kind: String,
    val baseJson: String,
    val localJson: String,
    val remoteJson: String,
    val localDeleted: Boolean,
    val remoteDeleted: Boolean,
    val createdAt: String = nowIso()
)

@Entity(tableName = "sync_state")
data class SyncStateEntity(
    @PrimaryKey val singletonId: Int = 1,
    val cursor: Long = 0,
    val lastSuccessAt: String? = null,
    val lastError: String? = null
)

@Entity(tableName = "sync_tombstone_acks")
data class SyncTombstoneAckEntity(
    @PrimaryKey val cursor: Long
)
