package com.insuranceai.android.sync

import com.insuranceai.android.data.local.SyncOutboxEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class FamilySyncContractTest {
    @Test
    fun syncRequestNegotiatesFamilyV1WithoutChangingOperationIdentity() {
        val pending = listOf(
            SyncOutboxEntity(
                operationId = "stable-operation-id",
                entityType = "customer",
                entityId = "customer-1",
                baseJson = "{}",
                patchJson = "{\"phone\":\"010\"}",
            )
        )

        val request = buildSyncRequest(cursor = 41, outbox = pending, tombstoneAcks = listOf(7L))

        assertEquals(2, request.getInt("schema_version"))
        assertTrue(request.getJSONArray("capabilities").toString().contains("family_relationship.v1"))
        assertEquals("stable-operation-id", request.getJSONArray("operations").getJSONObject(0).getString("operation_id"))
        assertEquals(41L, request.getLong("cursor"))
        assertEquals(7L, request.getJSONArray("tombstone_acks").getLong(0))
    }
}
