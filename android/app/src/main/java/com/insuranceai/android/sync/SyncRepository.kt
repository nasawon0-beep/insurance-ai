package com.insuranceai.android.sync

import androidx.room.withTransaction
import com.insuranceai.android.data.local.AppDatabase
import com.insuranceai.android.data.local.ConsultationEntity
import com.insuranceai.android.data.local.CoverageAnalysisEntity
import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.PolicyEntity
import com.insuranceai.android.data.local.PolicyCoverageEntity
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
) : PairingActions, ConflictResolutionActions, AddressContinuityActions, DiscoveredAddressActions {
    private val syncDao = database.syncDao()
    private val syncRunGate = SyncRunGate()

    fun observeConflicts(): Flow<List<SyncConflictEntity>> = syncDao.observeConflicts()
    fun observeState(): Flow<SyncStateEntity?> = syncDao.observeState()
    fun observePendingCount(): Flow<Int> = syncDao.observePendingCount()
    fun observePendingItems() = syncDao.observePendingItems()
    fun isConnectionConfigured(): Boolean = settings.isConfigured()
    fun connectionLabel(): String = settings.endpointLabel() ?: "등록된 PC 없음"
    override fun registeredPcInstanceId(): String? = settings.registeredPcInstanceId()

    private suspend fun recordBlocked(message: String) {
        val state = syncDao.state() ?: SyncStateEntity()
        syncDao.saveState(state.copy(lastError = message))
    }

    suspend fun recordWorkerFailure(message: String) = recordBlocked(message)

    private suspend fun clearBlockedAfterVerifiedRecovery() {
        val state = syncDao.state() ?: return
        if (state.lastError != null) syncDao.saveState(state.copy(lastError = null))
    }

    override suspend fun prepareCredentialStorage() {
        settings.verifyCredentialStorageAvailable()
    }

    override suspend fun pairAndSave(request: PairingRequest) {
        val provisional = SyncConnection(request.endpoint.trimEnd('/'), request.certificatePin, "")
        val body = JSONObject().put("code", request.code).put("device_name", request.deviceName)
        val response = client.post(provisional, "/v1/pair", body, authenticated = false)
        try {
            settings.save(
                request.endpoint,
                request.certificatePin,
                response.getString("token"),
                response.getString("device_id"),
                response.optString("pc_instance_id").takeIf(String::isNotBlank),
            )
        } catch (error: PairingLocalSaveException) {
            throw error
        } catch (error: Throwable) {
            throw PairingLocalSaveException(error)
        }
    }

    override suspend fun verifyAndUpdateAddress(request: AddressContinuityRequest) = syncRunGate.run {
        val old = settings.load() ?: error("페어링이 필요합니다")
        val candidate = SyncConnection(
            endpoint = request.endpoint.trimEnd('/'),
            certificateSha256 = request.certificatePin.lowercase().replace(":", ""),
            token = old.token,
            deviceId = old.deviceId,
        )
        val response = client.post(candidate, "/v1/continuity", JSONObject())
        val conflictIds = response.optJSONArray("unresolved_conflict_ids") ?: JSONArray()
        val proof = ContinuityProof(
            status = response.getString("status"),
            deviceId = response.getString("device_id"),
            unresolvedConflictIds = buildSet {
                for (index in 0 until conflictIds.length()) add(conflictIds.getString(index))
            },
            pcInstanceId = response.optString("pc_instance_id").takeIf(String::isNotBlank),
        )
        validateContinuityProof(
            expectedDeviceId = old.deviceId,
            localConflictIds = syncDao.conflicts().mapTo(mutableSetOf()) { it.id },
            proof = proof,
        )
        settings.updateVerifiedAddress(candidate.endpoint, candidate.certificateSha256, proof.deviceId, proof.pcInstanceId)
        clearBlockedAfterVerifiedRecovery()
    }

    override suspend fun verifyAndUpdateDiscoveredAddress(candidate: DiscoveredPc) = syncRunGate.run {
        val old = settings.load() ?: error("페어링이 필요합니다")
        require(mayProbeDiscoveredCandidate(old.pcInstanceId, candidate)) { "Discovered PC identity mismatch" }
        val discoveredConnection = old.copy(endpoint = candidate.endpoint.trimEnd('/'))
        val response = client.post(discoveredConnection, "/v1/continuity", JSONObject())
        val authenticatedInstance = response.getString("pc_instance_id")
        validateDiscoveredContinuity(old.pcInstanceId, candidate.instanceId, authenticatedInstance)
        val conflictIds = response.optJSONArray("unresolved_conflict_ids") ?: JSONArray()
        val proof = ContinuityProof(
            status = response.getString("status"),
            deviceId = response.getString("device_id"),
            unresolvedConflictIds = buildSet {
                for (index in 0 until conflictIds.length()) add(conflictIds.getString(index))
            },
            pcInstanceId = authenticatedInstance,
        )
        validateContinuityProof(
            expectedDeviceId = old.deviceId,
            localConflictIds = syncDao.conflicts().mapTo(mutableSetOf()) { it.id },
            proof = proof,
        )
        settings.updateVerifiedAddress(
            discoveredConnection.endpoint,
            old.certificateSha256,
            proof.deviceId,
            authenticatedInstance,
        )
        clearBlockedAfterVerifiedRecovery()
    }

    override suspend fun verifyCurrentAddress(): Boolean = syncRunGate.run {
        val current = settings.load() ?: return@run false
        val expectedInstance = current.pcInstanceId ?: return@run false
        val expectedDevice = current.deviceId ?: return@run false
        val response = client.post(current, "/v1/continuity", JSONObject())
        val authenticatedInstance = response.getString("pc_instance_id")
        validateDiscoveredContinuity(expectedInstance, expectedInstance, authenticatedInstance)
        val conflictIds = response.optJSONArray("unresolved_conflict_ids") ?: JSONArray()
        validateContinuityProof(
            expectedDeviceId = expectedDevice,
            localConflictIds = syncDao.conflicts().mapTo(mutableSetOf()) { it.id },
            proof = ContinuityProof(
                status = response.getString("status"),
                deviceId = response.getString("device_id"),
                unresolvedConflictIds = buildSet {
                    for (index in 0 until conflictIds.length()) add(conflictIds.getString(index))
                },
                pcInstanceId = authenticatedInstance,
            ),
        )
        clearBlockedAfterVerifiedRecovery()
        true
    }

    override suspend fun syncOnce(): Boolean = syncRunGate.run {
        val connection = settings.load() ?: return@run false
        try {
            syncOnceLocked(connection)
            true
        } catch (error: Exception) {
            recordBlocked(workerFailure(error).status)
            throw error
        }
    }

    private suspend fun syncOnceLocked(connection: SyncConnection): Set<String> {
        var rounds = 0
        var hasMore: Boolean
        var serverConflictIds = emptySet<String>()
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
            val conflicts = response.optJSONArray("conflicts") ?: JSONArray()
            serverConflictIds = buildSet {
                for (index in 0 until conflicts.length()) {
                    add(conflicts.getJSONObject(index).getString("id"))
                }
            }
            hasMore = response.optBoolean("has_more", false)
            rounds++
        } while (hasMore && rounds < 10)
        return serverConflictIds
    }

    override suspend fun resolveConflict(conflict: SyncConflictEntity, chooseLocal: Boolean): Boolean {
        val connection = settings.load() ?: error("페어링이 필요합니다")
        val sourceJson = JSONObject(if (chooseLocal) conflict.localJson else conflict.remoteJson)
        val source = sourceJson.keys().asSequence().associateWith { key ->
            if (sourceJson.isNull(key)) null else sourceJson.get(key)
        }
        val plan = buildConflictResolutionPlan(
            conflict,
            chooseLocal,
            selected = source,
            remoteHasNoFields = JSONObject(conflict.remoteJson).length() == 0,
        )
        plan.unavailableReason?.let { throw ConflictResolutionUnavailableException(it) }
        val request = checkNotNull(plan.request)
        return syncRunGate.run {
            val response = client.post(
                connection,
                request.path,
                JSONObject().put("resolution", JSONObject(request.resolution)).put("deleted", request.deleted)
            )
            response.getLong("cursor")
            check(response.getString("status") == "resolved") { "Server did not confirm resolution" }
            check(response.getString("conflict_id") == conflict.id) { "Conflict identity mismatch" }
            check(response.getString("entity_type") == conflict.entityType) { "Conflict type mismatch" }
            check(response.getString("entity_id") == conflict.entityId) { "Conflict entity mismatch" }
            connection.deviceId?.let { expected ->
                check(response.getString("device_id") == expected) { "Device identity mismatch" }
            }
            check(response.getBoolean("deleted") == request.deleted) { "Delete resolution mismatch" }
            val payload = response.getJSONObject("payload")
            request.resolution.forEach { (key, value) ->
                val matches = if (value == null) {
                    payload.has(key) && payload.isNull(key)
                } else {
                    payload.has(key) && payload.get(key).toString() == value.toString()
                }
                check(matches) { "Resolved value mismatch" }
            }

            val unresolved = conflict.id in syncOnceLocked(connection)
            if (!unresolved) syncDao.removeConflict(conflict.id)
            !unresolved
        }
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
                "policy_coverage" -> database.policyCoverageDao().deleteById(id)
                "consultation" -> database.consultationDao().deleteById(id)
                "coverage_analysis" -> database.coverageAnalysisDao().deleteById(id)
            }
            return
        }
        val payload = change.getJSONObject("payload")
        when (type) {
            "customer" -> {
                val old = database.customerDao().getById(id)
                val hasPendingLocalEdit = syncDao.pendingForEntity("customer", id) > 0
                database.customerDao().insert(CustomerEntity(
                    id = id,
                    name = payload.nullable("name") ?: old?.name ?: "이름 없음",
                    phone = if (hasPendingLocalEdit) old?.phone else payload.nullable("phone"),
                    birthDate = payload.nullable("birth_date") ?: old?.birthDate,
                    gender = payload.nullable("gender") ?: old?.gender,
                    email = payload.nullable("email") ?: old?.email,
                    addressEncrypted = if (hasPendingLocalEdit) old?.addressEncrypted else payload.nullable("address")?.let(encryption::encrypt),
                    occupation = payload.nullable("occupation") ?: old?.occupation,
                    tagsEncrypted = payload.nullable("tags")?.let(encryption::encrypt) ?: old?.tagsEncrypted,
                    memoEncrypted = if (hasPendingLocalEdit) old?.memoEncrypted else payload.nullable("memo")?.let(encryption::encrypt),
                    rrnEncrypted = old?.rrnEncrypted,
                    rrnHash = old?.rrnHash,
                    customerStatus = payload.nullable("customer_status") ?: old?.customerStatus,
                    importBatchId = old?.importBatchId,
                    createdAt = payload.nullable("created_at") ?: old?.createdAt ?: nowIso(),
                    updatedAt = payload.nullable("updated_at") ?: nowIso(),
                    syncStatus = if (hasPendingLocalEdit) com.insuranceai.android.data.local.SYNC_PENDING else SYNC_SYNCED
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
                documentStatus = payload.nullable("document_status"),
                sourceDocumentType = payload.nullable("source_document_type"),
                insuredPeriodEncrypted = payload.nullable("insured_period")?.let(encryption::encrypt),
                paymentPeriodEncrypted = payload.nullable("payment_period")?.let(encryption::encrypt),
                isOwn = payload.optBooleanOrNull("is_own"),
                createdAt = payload.nullable("created_at") ?: nowIso(),
                updatedAt = payload.nullable("updated_at") ?: nowIso(),
                syncStatus = SYNC_SYNCED
            ))
            "policy_coverage" -> database.policyCoverageDao().insert(PolicyCoverageEntity(
                id = id,
                customerId = payload.getString("customer_id"),
                policyId = payload.nullable("policy_id"),
                insurer = payload.nullable("insurer"),
                productName = payload.nullable("product_name"),
                riderName = payload.nullable("rider_name"),
                standardName = payload.nullable("standard_name"),
                amount = payload.optLongOrNull("amount"),
                amountText = payload.nullable("amount_text"),
                amountStatus = payload.nullable("amount_status"),
                documentStatus = payload.nullable("document_status"),
                startDate = payload.nullable("start_date"),
                endDate = payload.nullable("end_date"),
                rawPayloadEncrypted = encryption.encrypt(payload.toString()),
                updatedAt = payload.nullable("updated_at") ?: nowIso(),
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
                summaryJsonEncrypted = encryption.encrypt(payload.get("summary").toString()),
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

    private fun JSONObject.optLongOrNull(name: String): Long? =
        if (!has(name) || isNull(name)) null else getLong(name)

    private fun JSONObject.optBooleanOrNull(name: String): Boolean? =
        if (!has(name) || isNull(name)) null else getBoolean(name)
}
