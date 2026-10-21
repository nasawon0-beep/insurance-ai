package com.insuranceai.android.sync

import androidx.room.withTransaction
import com.insuranceai.android.data.local.AppDatabase
import com.insuranceai.android.data.local.ConsultationEntity
import com.insuranceai.android.data.local.CoverageAnalysisEntity
import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.PolicyEntity
import com.insuranceai.android.data.local.SYNC_SYNCED
import com.insuranceai.android.data.local.SyncConflictEntity
import com.insuranceai.android.data.local.SyncStateEntity
import com.insuranceai.android.data.local.SyncTombstoneAckEntity
import com.insuranceai.android.data.local.nowIso
import com.insuranceai.android.security.EncryptionService
import org.json.JSONArray
import org.json.JSONObject
import kotlinx.coroutines.flow.Flow

class SyncRepository(
    private val database: AppDatabase,
    private val encryption: EncryptionService,
    private val settings: SyncSettings,
    private val client: LanSyncClient = LanSyncClient()
) {
    private val syncDao = database.syncDao()

    fun observeConflicts(): Flow<List<SyncConflictEntity>> = syncDao.observeConflicts()

    suspend fun pair(endpoint: String, certificatePin: String, code: String, deviceName: String) {
        val provisional = SyncConnection(endpoint.trimEnd('/'), certificatePin, "")
        val body = JSONObject().put("code", code).put("device_name", deviceName)
        val response = client.post(provisional, "/v1/pair", body, authenticated = false)
        settings.save(endpoint, certificatePin, response.getString("token"))
    }

    suspend fun syncOnce(): Boolean {
        val connection = settings.load() ?: return false
        var rounds = 0
        var hasMore: Boolean
        do {
            val state = syncDao.state() ?: SyncStateEntity()
            val outbox = syncDao.pending()
            val priorAcks = syncDao.pendingTombstoneAcks()
            if (outbox.isNotEmpty()) syncDao.markAttempted(outbox.map { it.operationId })
            val operations = JSONArray()
            outbox.forEach { item ->
                operations.put(JSONObject()
                    .put("operation_id", item.operationId)
                    .put("entity_type", item.entityType)
                    .put("entity_id", item.entityId)
                    .put("base", JSONObject(item.baseJson))
                    .put("patch", JSONObject(item.patchJson))
                    .put("deleted", item.deleted))
            }
            val body = JSONObject()
                .put("cursor", state.cursor)
                .put("limit", 200)
                .put("operations", operations)
                .put("tombstone_acks", JSONArray(priorAcks))
            val response = client.post(connection, "/v1/sync", body)
            applyResponse(response, priorAcks)
            hasMore = response.optBoolean("has_more", false)
            rounds++
        } while (hasMore && rounds < 10)
        return true
    }

    suspend fun resolveConflict(conflict: SyncConflictEntity, chooseLocal: Boolean) {
        val connection = settings.load() ?: error("페어링이 필요합니다")
        val source = JSONObject(if (chooseLocal) conflict.localJson else conflict.remoteJson)
        val deleted = if (chooseLocal) conflict.localDeleted else conflict.remoteDeleted
        val allowed = when (conflict.entityType) {
            "customer" -> setOf("phone", "address", "memo")
            "consultation" -> setOf("customer_id", "consulted_at", "channel", "title", "content", "follow_up_at", "follow_up_done_at")
            else -> emptySet()
        }
        val resolution = JSONObject()
        source.keys().forEach { key -> if (key in allowed) resolution.put(key, source.get(key)) }
        client.post(
            connection,
            "/v1/conflicts/${conflict.id}/resolve",
            JSONObject().put("resolution", resolution).put("deleted", deleted)
        )
        syncDao.removeConflict(conflict.id)
        syncOnce()
    }

    private suspend fun applyResponse(response: JSONObject, acknowledged: List<Long>) {
        database.withTransaction {
            val completed = mutableListOf<String>()
            val results = response.optJSONArray("results") ?: JSONArray()
            for (index in 0 until results.length()) {
                val result = results.getJSONObject(index)
                if (result.getString("status") in setOf("applied", "conflict")) {
                    completed += result.getString("operation_id")
                }
            }
            if (completed.isNotEmpty()) syncDao.removeApplied(completed)
            if (acknowledged.isNotEmpty()) syncDao.removeTombstoneAcks(acknowledged)

            val changes = response.optJSONArray("changes") ?: JSONArray()
            for (index in 0 until changes.length()) {
                val change = changes.getJSONObject(index)
                applyChange(change)
                if (change.optBoolean("deleted")) {
                    syncDao.saveTombstoneAck(SyncTombstoneAckEntity(change.getLong("cursor")))
                }
            }
            val conflicts = response.optJSONArray("conflicts") ?: JSONArray()
            for (index in 0 until conflicts.length()) {
                val conflict = conflicts.getJSONObject(index)
                syncDao.saveConflict(SyncConflictEntity(
                    id = conflict.getString("id"),
                    entityType = conflict.getString("entity_type"),
                    entityId = conflict.getString("entity_id"),
                    kind = conflict.getString("kind"),
                    baseJson = conflict.getJSONObject("base").toString(),
                    localJson = conflict.getJSONObject("local").toString(),
                    remoteJson = conflict.getJSONObject("remote").toString(),
                    localDeleted = conflict.optBoolean("local_deleted"),
                    remoteDeleted = conflict.optBoolean("remote_deleted")
                ))
            }
            syncDao.saveState(SyncStateEntity(
                cursor = response.getLong("next_cursor"),
                lastSuccessAt = nowIso(),
                lastError = null
            ))
        }
    }

    private suspend fun applyChange(change: JSONObject) {
        val type = change.getString("entity_type")
        val id = change.getString("entity_id")
        if (change.optBoolean("deleted")) {
            when (type) {
                "customer" -> database.customerDao().deleteById(id)
                "policy" -> database.policyDao().deleteById(id)
                "consultation" -> database.consultationDao().deleteById(id)
                "coverage_analysis" -> database.coverageAnalysisDao().deleteById(id)
            }
            return
        }
        val payload = change.getJSONObject("payload")
        when (type) {
            "customer" -> {
                val old = database.customerDao().getById(id)
                database.customerDao().insert(CustomerEntity(
                    id = id,
                    name = payload.nullable("name") ?: old?.name ?: "이름 없음",
                    phone = payload.nullable("phone"),
                    birthDate = payload.nullable("birth_date") ?: old?.birthDate,
                    gender = payload.nullable("gender") ?: old?.gender,
                    email = payload.nullable("email") ?: old?.email,
                    addressEncrypted = payload.nullable("address")?.let(encryption::encrypt),
                    occupation = payload.nullable("occupation") ?: old?.occupation,
                    tagsEncrypted = payload.nullable("tags")?.let(encryption::encrypt) ?: old?.tagsEncrypted,
                    memoEncrypted = payload.nullable("memo")?.let(encryption::encrypt),
                    rrnEncrypted = old?.rrnEncrypted,
                    rrnHash = old?.rrnHash,
                    customerStatus = payload.nullable("customer_status") ?: old?.customerStatus,
                    importBatchId = old?.importBatchId,
                    createdAt = payload.nullable("created_at") ?: old?.createdAt ?: nowIso(),
                    updatedAt = payload.nullable("updated_at") ?: nowIso(),
                    syncStatus = SYNC_SYNCED
                ))
            }
            "policy" -> database.policyDao().insert(PolicyEntity(
                id = id,
                customerId = payload.getString("customer_id"),
                insurer = payload.nullable("insurer"),
                productName = payload.nullable("product_name"),
                policyNumber = payload.nullable("policy_number"),
                planType = payload.nullable("plan_type"),
                premium = payload.optIntOrNull("premium"),
                paymentCycle = payload.nullable("payment_cycle"),
                startDate = payload.nullable("start_date"),
                endDate = payload.nullable("end_date"),
                status = payload.nullable("status") ?: "ACTIVE",
                memoEncrypted = payload.nullable("memo")?.let(encryption::encrypt),
                documentId = payload.nullable("document_id"),
                insuredPeriodEncrypted = payload.nullable("insured_period")?.let(encryption::encrypt),
                paymentPeriodEncrypted = payload.nullable("payment_period")?.let(encryption::encrypt),
                isOwn = payload.optBooleanOrNull("is_own"),
                createdAt = payload.nullable("created_at") ?: nowIso(),
                updatedAt = payload.nullable("updated_at") ?: nowIso(),
                syncStatus = SYNC_SYNCED
            ))
            "consultation" -> database.consultationDao().insert(ConsultationEntity(
                id = id,
                customerId = payload.getString("customer_id"),
                consultedAt = payload.nullable("consulted_at") ?: nowIso(),
                channel = payload.nullable("channel"),
                title = payload.nullable("title"),
                contentEncrypted = payload.nullable("content")?.let(encryption::encrypt),
                transcriptEncrypted = null,
                followUpAt = payload.nullable("follow_up_at"),
                followUpDoneAt = payload.nullable("follow_up_done_at"),
                createdAt = payload.nullable("created_at") ?: nowIso(),
                updatedAt = payload.nullable("updated_at") ?: nowIso(),
                syncStatus = SYNC_SYNCED
            ))
            "coverage_analysis" -> database.coverageAnalysisDao().insert(CoverageAnalysisEntity(
                id = id,
                customerId = payload.getString("customer_id"),
                summaryJsonEncrypted = encryption.encrypt(payload.getJSONObject("summary").toString()),
                generatedAt = payload.getString("generated_at"),
                sourceHash = payload.nullable("source_hash"),
                updatedAt = payload.nullable("updated_at") ?: nowIso()
            ))
        }
    }

    private fun JSONObject.nullable(name: String): String? =
        if (!has(name) || isNull(name)) null else get(name).toString()

    private fun JSONObject.optIntOrNull(name: String): Int? =
        if (!has(name) || isNull(name)) null else getInt(name)

    private fun JSONObject.optBooleanOrNull(name: String): Boolean? =
        if (!has(name) || isNull(name)) null else getBoolean(name)
}
