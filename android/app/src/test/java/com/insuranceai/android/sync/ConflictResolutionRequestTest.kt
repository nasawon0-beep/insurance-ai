package com.insuranceai.android.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import com.insuranceai.android.data.local.SyncConflictEntity

class ConflictResolutionRequestTest {
    @Test
    fun orphanedCustomerCannotBeRestoredAndPcChoiceBecomesDeletion() {
        val conflict = SyncConflictEntity(
            id = "conflict-orphan",
            entityType = "customer",
            entityId = "customer-only-on-mobile",
            kind = "field",
            baseJson = """{"phone":"010-0000-0000","address":"합성 기준 주소","memo":"기준"}""",
            localJson = """{"phone":"010-0000-0000","address":"합성 기준 주소","memo":"합성 모바일 -폰수정"}""",
            remoteJson = "{}",
            localDeleted = false,
            remoteDeleted = false,
        )

        val mobile = buildConflictResolutionPlan(
            conflict,
            chooseLocal = true,
            selected = mapOf("phone" to "010-0000-0000", "address" to "합성 기준 주소", "memo" to "합성 모바일 -폰수정"),
            remoteHasNoFields = true,
        )
        val pc = buildConflictResolutionPlan(
            conflict,
            chooseLocal = false,
            selected = emptyMap(),
            remoteHasNoFields = true,
        )

        assertEquals("PC에 없는 고객은 모바일 값으로 복원할 수 없습니다 · PC 값 선택으로 정리하세요", mobile.unavailableReason)
        assertEquals(null, mobile.request)
        assertTrue(pc.request!!.deleted)
        assertEquals(emptyMap<String, Any?>(), pc.request.resolution)
    }

    @Test
    fun mobileChoiceBuildsExactResolvePathAndAllowedCustomerPatch() {
        val request = buildConflictResolutionRequest(
            conflictId = "conflict-1",
            entityType = "customer",
            source = mapOf("memo" to "모바일", "name" to "수정 금지", "private" to "제외"),
            deleted = false,
        )

        assertEquals("/v1/conflicts/conflict-1/resolve", request.path)
        assertEquals(mapOf("memo" to "모바일"), request.resolution)
        assertFalse(request.deleted)
    }

    @Test
    fun pcChoiceUsesRemoteAllowedFieldsWithoutInventingMissingData() {
        val request = buildConflictResolutionRequest(
            conflictId = "conflict-2",
            entityType = "customer",
            source = mapOf("name" to "합성 고객", "phone" to null, "memo" to "PC"),
            deleted = false,
        )

        assertEquals(mapOf("phone" to null, "memo" to "PC"), request.resolution)
    }
}
