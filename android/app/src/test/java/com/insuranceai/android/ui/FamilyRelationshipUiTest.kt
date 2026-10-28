package com.insuranceai.android.ui

import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.FamilyRelationshipEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class FamilyRelationshipUiTest {
    @Test
    fun rowsAreDerivedBidirectionallyAndKeepDuplicateNamesDistinctById() {
        val customers = listOf(
            CustomerEntity(id = "a", name = "김가족", phone = "010-0000-0001"),
            CustomerEntity(id = "b", name = "김가족", phone = "010-0000-0002"),
        )
        val edge = FamilyRelationshipEntity(
            id = "edge-1",
            customerAId = "a",
            customerBId = "b",
            relationAToB = "PARENT",
            updatedAt = "2026-10-27T00:00:00Z",
        )

        val fromA = familyUiRows("a", customers, listOf(edge)).single()
        val fromB = familyUiRows("b", customers, listOf(edge)).single()

        assertEquals("b", fromA.customerId)
        assertEquals("부모", fromA.relationshipLabel)
        assertTrue(fromA.customerLabel.contains("0002"))
        assertEquals("a", fromB.customerId)
        assertEquals("자녀", fromB.relationshipLabel)
        assertTrue(fromB.customerLabel.contains("0001"))
    }

    @Test
    fun otherDescriptionIsShownWithoutExposingInternalRelationshipCode() {
        val customers = listOf(CustomerEntity(id = "a", name = "A"), CustomerEntity(id = "b", name = "B"))
        val edge = FamilyRelationshipEntity(
            id = "edge-2",
            customerAId = "a",
            customerBId = "b",
            relationAToB = "OTHER",
            otherDescription = "법정대리인",
            updatedAt = "2026-10-27T00:00:00Z",
        )

        assertEquals("기타 · 법정대리인", familyUiRows("a", customers, listOf(edge)).single().relationshipLabel)
    }
}
