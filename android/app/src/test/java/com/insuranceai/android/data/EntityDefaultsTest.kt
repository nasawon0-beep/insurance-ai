package com.insuranceai.android.data

import com.insuranceai.android.data.local.ConsultationEntity
import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.PolicyEntity
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class EntityDefaultsTest {
    @Test
    fun newEntitiesStartPendingSyncAndHaveUniqueIds() {
        val customerA = CustomerEntity(name = "김고객")
        val customerB = CustomerEntity(name = "이고객")
        val policy = PolicyEntity(customerId = customerA.id)
        val consultation = ConsultationEntity(customerId = customerA.id, consultedAt = "2026-09-23T10:00:00Z")

        assertNotEquals(customerA.id, customerB.id)
        assertEquals(1, customerA.syncStatus)
        assertEquals(1, policy.syncStatus)
        assertEquals(1, consultation.syncStatus)
    }
}
