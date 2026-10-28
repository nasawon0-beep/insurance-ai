package com.insuranceai.android.ui

import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.FamilyRelationshipEntity

data class FamilyRelationshipRow(
    val edgeId: String,
    val customerId: String,
    val customerLabel: String,
    val relationshipLabel: String,
)

private val inverseFamilyRelationship = mapOf(
    "SPOUSE" to "SPOUSE",
    "PARENT" to "CHILD",
    "CHILD" to "PARENT",
    "SIBLING" to "SIBLING",
    "OTHER" to "OTHER",
)

private val familyRelationshipLabels = mapOf(
    "SPOUSE" to "배우자",
    "PARENT" to "부모",
    "CHILD" to "자녀",
    "SIBLING" to "형제자매",
    "OTHER" to "기타",
)

fun familyUiRows(
    selectedCustomerId: String,
    customers: List<CustomerEntity>,
    relationships: List<FamilyRelationshipEntity>,
): List<FamilyRelationshipRow> {
    val customersById = customers.associateBy { it.id }
    return relationships.mapNotNull { edge ->
        val isA = edge.customerAId == selectedCustomerId
        if (!isA && edge.customerBId != selectedCustomerId) return@mapNotNull null
        val relatedId = if (isA) edge.customerBId else edge.customerAId
        val customer = customersById[relatedId] ?: return@mapNotNull null
        val relation = if (isA) edge.relationAToB else inverseFamilyRelationship[edge.relationAToB]
        val baseLabel = familyRelationshipLabels[relation] ?: "관계 확인 필요"
        val relationLabel = if (relation == "OTHER" && !edge.otherDescription.isNullOrBlank()) {
            "$baseLabel · ${edge.otherDescription}"
        } else baseLabel
        FamilyRelationshipRow(
            edgeId = edge.id,
            customerId = relatedId,
            customerLabel = customerChoice(customer).label,
            relationshipLabel = relationLabel,
        )
    }.sortedWith(compareBy({ it.relationshipLabel }, { it.customerLabel }, { it.customerId }))
}
