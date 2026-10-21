package com.insuranceai.android.data.local

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class CustomerUpsertPreservesChildrenTest {
    @Test
    fun updatingCustomerDoesNotCascadeDeleteExistingChildren() = runBlocking {
        val database = Room.inMemoryDatabaseBuilder(
            ApplicationProvider.getApplicationContext(),
            AppDatabase::class.java
        ).allowMainThreadQueries().build()
        try {
            database.customerDao().insert(CustomerEntity(id = "customer-1", name = "before"))
            database.policyDao().insert(PolicyEntity(id = "policy-1", customerId = "customer-1"))
            database.consultationDao().insert(
                ConsultationEntity(
                    id = "consultation-1",
                    customerId = "customer-1",
                    consultedAt = "2026-10-21T00:00:00Z"
                )
            )
            database.coverageAnalysisDao().insert(
                CoverageAnalysisEntity(
                    id = "coverage-1",
                    customerId = "customer-1",
                    summaryJsonEncrypted = "encrypted",
                    generatedAt = "2026-10-21T00:00:00Z",
                    updatedAt = "2026-10-21T00:00:00Z"
                )
            )

            database.customerDao().insert(CustomerEntity(id = "customer-1", name = "after"))

            assertEquals("after", database.customerDao().getById("customer-1")?.name)
            assertEquals("policy-1", database.policyDao().getById("policy-1")?.id)
            assertEquals("consultation-1", database.consultationDao().getById("consultation-1")?.id)
            assertEquals(1, database.coverageAnalysisDao().observeAll().first().size)
        } finally {
            database.close()
        }
    }
}
