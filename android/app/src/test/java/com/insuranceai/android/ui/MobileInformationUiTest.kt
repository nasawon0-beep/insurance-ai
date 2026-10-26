package com.insuranceai.android.ui

import com.insuranceai.android.data.local.PolicyEntity
import com.insuranceai.android.data.local.SYNC_PENDING
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MobileInformationUiTest {
    @Test
    fun policyClassificationUsesMetadataAndNeverHidesLegacyRows() {
        val confirmed = PolicyEntity(id = "p1", customerId = "c", documentStatus = "CONFIRMED", sourceDocumentType = "proposal")
        val reference = PolicyEntity(id = "p2", customerId = "c", documentStatus = "REFERENCE", sourceDocumentType = "product_description")
        val analysis = PolicyEntity(id = "p3", customerId = "c", documentStatus = "CONFIRMED", sourceDocumentType = "coverage_analysis_full")
        val legacy = PolicyEntity(id = "p4", customerId = "c")

        assertEquals(PolicyDisplayGroup.CONTRACT, policyDisplayGroup(confirmed))
        assertEquals(PolicyDisplayGroup.REVIEW, policyDisplayGroup(reference))
        assertEquals(PolicyDisplayGroup.REVIEW, policyDisplayGroup(analysis))
        assertEquals(PolicyDisplayGroup.UNCLASSIFIED, policyDisplayGroup(legacy))
        assertTrue(policyClassificationLabel(legacy).contains("분류 미확인"))
    }

    @Test
    fun liveOutboxAndConflictOverrideStaleCachedStatus() {
        assertEquals(UiSyncState.SYNCED, entitySyncState("customer", "c1", SYNC_PENDING, emptySet(), emptySet()))
        assertEquals(UiSyncState.PENDING, entitySyncState("customer", "c1", 0, setOf(EntityKey("customer", "c1")), emptySet()))
        assertEquals(UiSyncState.CONFLICT, entitySyncState("customer", "c1", 0, setOf(EntityKey("customer", "c1")), setOf(EntityKey("customer", "c1"))))
        assertFalse(entitySyncLabel(UiSyncState.CONFLICT).contains("대기"))
    }

    @Test
    fun amountFormattingDistinguishesMissingNullZeroAndUnknownUnit() {
        assertEquals("미제공", coverageAmountMissing().display)
        assertEquals("값 없음", coverageAmountNull().display)
        assertEquals("0원", coverageAmountValue(0).display)
        assertEquals("12 USD · 형식 확인 필요", coverageAmountValue("12 USD").display)
    }
}
