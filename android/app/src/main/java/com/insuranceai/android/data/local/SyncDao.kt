package com.insuranceai.android.data.local

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface SyncDao {
    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun enqueue(item: SyncOutboxEntity)

    @Query("SELECT * FROM sync_outbox ORDER BY localId LIMIT :limit")
    suspend fun pending(limit: Int = 200): List<SyncOutboxEntity>

    @Query("DELETE FROM sync_outbox WHERE operationId IN (:operationIds)")
    suspend fun removeApplied(operationIds: List<String>)

    @Query("UPDATE sync_outbox SET attemptCount = attemptCount + 1 WHERE operationId IN (:operationIds)")
    suspend fun markAttempted(operationIds: List<String>)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveConflict(conflict: SyncConflictEntity)

    @Query("SELECT * FROM sync_conflicts ORDER BY createdAt")
    fun observeConflicts(): Flow<List<SyncConflictEntity>>

    @Query("DELETE FROM sync_conflicts WHERE id = :id")
    suspend fun removeConflict(id: String)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveState(state: SyncStateEntity)

    @Query("SELECT * FROM sync_state WHERE singletonId = 1")
    suspend fun state(): SyncStateEntity?

    @Query("SELECT * FROM sync_state WHERE singletonId = 1")
    fun observeState(): Flow<SyncStateEntity?>

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun saveTombstoneAck(ack: SyncTombstoneAckEntity)

    @Query("SELECT cursor FROM sync_tombstone_acks ORDER BY cursor")
    suspend fun pendingTombstoneAcks(): List<Long>

    @Query("DELETE FROM sync_tombstone_acks WHERE cursor IN (:cursors)")
    suspend fun removeTombstoneAcks(cursors: List<Long>)
}
