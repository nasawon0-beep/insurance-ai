package com.insuranceai.android.sync

data class FieldConflict(
    val field: String,
    val base: String?,
    val local: String?,
    val remote: String?
)

data class MergeResult(
    val merged: Map<String, String?>,
    val conflicts: List<FieldConflict>
)

/** Deterministic base/local/remote merge. Timestamps are diagnostics only. */
object ThreeWayMerge {
    fun merge(
        base: Map<String, String?>,
        remote: Map<String, String?>,
        localPatch: Map<String, String?>,
        @Suppress("UNUSED_PARAMETER") clientTime: String? = null,
        @Suppress("UNUSED_PARAMETER") serverTime: String? = null
    ): MergeResult {
        val merged = remote.toMutableMap()
        val conflicts = mutableListOf<FieldConflict>()
        localPatch.forEach { (field, local) ->
            val baseValue = base[field]
            val remoteValue = remote[field]
            if (remoteValue != baseValue && local != remoteValue) {
                conflicts += FieldConflict(field, baseValue, local, remoteValue)
            } else {
                merged[field] = local
            }
        }
        return MergeResult(merged, conflicts)
    }
}
