package com.insuranceai.android.sync

import com.insuranceai.android.data.local.SyncConflictEntity

data class ConflictResolutionRequest(
    val path: String,
    val resolution: Map<String, Any?>,
    val deleted: Boolean,
)

data class ConflictResolutionPlan(
    val request: ConflictResolutionRequest? = null,
    val unavailableReason: String? = null,
)

class ConflictResolutionUnavailableException(message: String) : IllegalStateException(message)

fun buildConflictResolutionRequest(
    conflictId: String,
    entityType: String,
    source: Map<String, Any?>,
    deleted: Boolean,
): ConflictResolutionRequest {
    val allowed = when (entityType) {
        "customer" -> setOf("phone", "address", "memo")
        "consultation" -> setOf(
            "customer_id", "consulted_at", "channel", "title", "content",
            "follow_up_at", "follow_up_done_at",
        )
        else -> emptySet()
    }
    return ConflictResolutionRequest(
        path = "/v1/conflicts/$conflictId/resolve",
        resolution = source.filterKeys(allowed::contains),
        deleted = deleted,
    )
}

fun buildConflictResolutionPlan(
    conflict: SyncConflictEntity,
    chooseLocal: Boolean,
    selected: Map<String, Any?>,
    remoteHasNoFields: Boolean,
): ConflictResolutionPlan {
    val remoteMissing = conflict.entityType == "customer" &&
        (conflict.remoteDeleted || remoteHasNoFields)
    if (chooseLocal && remoteMissing) {
        return ConflictResolutionPlan(
            unavailableReason = "PC에 없는 고객은 모바일 값으로 복원할 수 없습니다 · PC 값 선택으로 정리하세요",
        )
    }
    return ConflictResolutionPlan(
        request = buildConflictResolutionRequest(
            conflictId = conflict.id,
            entityType = conflict.entityType,
            source = selected,
            deleted = if (chooseLocal) conflict.localDeleted else conflict.remoteDeleted || remoteMissing,
        ),
    )
}
