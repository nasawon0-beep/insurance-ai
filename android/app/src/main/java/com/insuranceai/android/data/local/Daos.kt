package com.insuranceai.android.data.local

import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import kotlinx.coroutines.flow.Flow

@Dao
interface CustomerDao {
    @Query("SELECT * FROM customers ORDER BY updatedAt DESC")
    fun observeAll(): Flow<List<CustomerEntity>>

    @Query("SELECT * FROM customers WHERE name LIKE '%' || :query || '%' OR phone LIKE '%' || :query || '%' ORDER BY updatedAt DESC")
    fun search(query: String): Flow<List<CustomerEntity>>

    @Query("SELECT * FROM customers WHERE id = :id")
    suspend fun getById(id: String): CustomerEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(customer: CustomerEntity)

    @Update
    suspend fun update(customer: CustomerEntity)

    @Delete
    suspend fun delete(customer: CustomerEntity)
}

@Dao
interface PolicyDao {
    @Query("SELECT * FROM policies ORDER BY updatedAt DESC")
    fun observeAll(): Flow<List<PolicyEntity>>

    @Query("SELECT * FROM policies WHERE customerId = :customerId ORDER BY updatedAt DESC")
    fun observeForCustomer(customerId: String): Flow<List<PolicyEntity>>

    @Query("SELECT COALESCE(SUM(premium), 0) FROM policies WHERE customerId = :customerId AND status = 'ACTIVE'")
    fun observeMonthlyPremium(customerId: String): Flow<Int>

    @Query("SELECT * FROM policies WHERE id = :id")
    suspend fun getById(id: String): PolicyEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(policy: PolicyEntity)

    @Update
    suspend fun update(policy: PolicyEntity)

    @Delete
    suspend fun delete(policy: PolicyEntity)
}

@Dao
interface ConsultationDao {
    @Query("SELECT * FROM consultations ORDER BY consultedAt DESC")
    fun observeAll(): Flow<List<ConsultationEntity>>

    @Query("SELECT * FROM consultations WHERE customerId = :customerId ORDER BY consultedAt DESC")
    fun observeForCustomer(customerId: String): Flow<List<ConsultationEntity>>

    @Query("SELECT * FROM consultations WHERE followUpDoneAt IS NULL AND followUpAt IS NOT NULL AND followUpAt <= :until ORDER BY followUpAt ASC")
    suspend fun getFollowUpDue(until: String): List<ConsultationEntity>

    @Query("SELECT * FROM consultations WHERE id = :id")
    suspend fun getById(id: String): ConsultationEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(consultation: ConsultationEntity)

    @Update
    suspend fun update(consultation: ConsultationEntity)

    @Delete
    suspend fun delete(consultation: ConsultationEntity)
}
