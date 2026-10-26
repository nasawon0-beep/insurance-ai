package com.insuranceai.android.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConflictUiModelsTest {
    @Test
    fun deleteConfirmationCancelAndDismissSendNoRequest() {
        val conflict = missingCustomerConflict("conflict-1", "customer-1")
        val coordinator = MissingCustomerDeleteConfirmationCoordinator()
        val requested = mutableListOf<String>()

        coordinator.request(conflict, remoteMissing = true)
        coordinator.dismiss()
        assertFalse(coordinator.confirm(listOf(conflict)) { requested += it.id })

        coordinator.request(conflict, remoteMissing = true)
        coordinator.dismiss()
        assertTrue(requested.isEmpty())
    }

    @Test
    fun deleteConfirmationOnlySendsRequestForExactUnchangedTargetOnce() {
        val target = missingCustomerConflict("conflict-target", "customer-target")
        val other = missingCustomerConflict("conflict-other", "customer-other")
        val changed = target.copy(remoteJson = "{\"name\":\"now-on-pc\"}")
        val coordinator = MissingCustomerDeleteConfirmationCoordinator()
        val requested = mutableListOf<String>()

        coordinator.request(target, remoteMissing = true)
        assertFalse(coordinator.request(other, remoteMissing = true))
        assertFalse(coordinator.confirm(listOf(changed, other)) { requested += it.id })
        coordinator.request(target, remoteMissing = true)
        assertTrue(coordinator.confirm(listOf(target, other)) { requested += it.id })
        assertFalse(coordinator.confirm(listOf(target, other)) { requested += it.id })

        assertEquals(listOf("conflict-target"), requested)
    }

    @Test
    fun ordinaryConflictNeverEntersDeleteConfirmation() {
        val ordinary = missingCustomerConflict("conflict-normal", "customer-normal").copy(
            remoteJson = "{\"name\":\"PC 고객\",\"memo\":\"PC\"}",
        )
        val coordinator = MissingCustomerDeleteConfirmationCoordinator()

        assertFalse(coordinator.request(ordinary, remoteMissing = false))
        assertEquals(null, coordinator.target.value)
    }

    @Test
    fun emptyRemoteIsMarkedAsMissingCanonicalCustomer() {
        val card = customerConflictCardUi(
            base = mapOf("phone" to "010-0000-0000"),
            local = mapOf("phone" to "010-0000-0000", "memo" to "폰수정"),
            remote = emptyMap(),
        )

        assertTrue(card.remoteMissing)
    }

    @Test
    fun fieldConflictShowsCustomerNameAndOnlyActualMemoValues() {
        val card = customerConflictCardUi(
            base = mapOf("name" to "합성 고객", "phone" to "010-0000", "memo" to "기준"),
            local = mapOf("memo" to "모바일"),
            remote = mapOf("name" to "합성 고객", "phone" to "010-0000", "memo" to "PC"),
        )

        assertEquals("합성 고객", card.customerName)
        assertEquals(listOf(ConflictFieldUi("메모", "모바일", "PC")), card.fields)
    }

    @Test
    fun missingValuesStayMissingInsteadOfBeingInvented() {
        val card = customerConflictCardUi(
            base = mapOf("name" to "합성 고객", "address" to "기준 주소"),
            local = mapOf("address" to null),
            remote = mapOf("name" to "합성 고객", "address" to "PC 주소"),
        )

        assertEquals(listOf(ConflictFieldUi("주소", null, "PC 주소")), card.fields)
    }

    private fun missingCustomerConflict(id: String, entityId: String) =
        com.insuranceai.android.data.local.SyncConflictEntity(
            id = id,
            entityType = "customer",
            entityId = entityId,
            kind = "delete_modify",
            baseJson = "{\"memo\":\"기준\"}",
            localJson = "{\"memo\":\"모바일\"}",
            remoteJson = "{}",
            localDeleted = false,
            remoteDeleted = true,
        )
}
