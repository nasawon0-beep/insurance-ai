package com.insuranceai.android.ui

import com.insuranceai.android.data.local.PolicyEntity
import com.insuranceai.android.data.local.SYNC_CONFLICT
import com.insuranceai.android.data.local.SYNC_PENDING

enum class PolicyDisplayGroup { CONTRACT, REVIEW, UNCLASSIFIED }

fun policyDisplayGroup(policy: PolicyEntity): PolicyDisplayGroup {
    val source = policy.sourceDocumentType?.lowercase()
    val status = policy.documentStatus?.uppercase()
    return when {
        source == null && status == null -> PolicyDisplayGroup.UNCLASSIFIED
        source in setOf("coverage_analysis_full", "coverage_analysis_short", "coverage_analysis_detail_only", "product_description") -> PolicyDisplayGroup.REVIEW
        status in setOf("REFERENCE", "PENDING_REVIEW", "PROPOSED") -> PolicyDisplayGroup.REVIEW
        status == "CONFIRMED" -> PolicyDisplayGroup.CONTRACT
        else -> PolicyDisplayGroup.UNCLASSIFIED
    }
}

fun policyClassificationLabel(policy: PolicyEntity): String = when (policyDisplayGroup(policy)) {
    PolicyDisplayGroup.CONTRACT -> "보험계약"
    PolicyDisplayGroup.REVIEW -> when (policy.sourceDocumentType?.lowercase()) {
        "coverage_analysis_full" -> "전체 보장분석 자료"
        "coverage_analysis_short" -> "간편 보장분석 자료"
        "coverage_analysis_detail_only" -> "보장 상세 자료"
        "product_description" -> "상품 참고 자료"
        else -> policyStatusLabel(policy.documentStatus)
    }
    PolicyDisplayGroup.UNCLASSIFIED -> "분류 미확인 · PC에서 문서 종류 확인 필요"
}

data class EntityKey(val type: String, val id: String)
enum class UiSyncState { SYNCED, PENDING, CONFLICT, UNKNOWN }

fun entitySyncState(
    entityType: String,
    entityId: String,
    cachedStatus: Int,
    pending: Set<EntityKey>,
    conflicts: Set<EntityKey>,
): UiSyncState {
    val key = EntityKey(entityType, entityId)
    return when {
        key in conflicts -> UiSyncState.CONFLICT
        key in pending -> UiSyncState.PENDING
        cachedStatus == SYNC_CONFLICT -> UiSyncState.CONFLICT
        // A stale cached pending flag must not outlive the authoritative outbox.
        cachedStatus == SYNC_PENDING -> UiSyncState.SYNCED
        cachedStatus == 0 -> UiSyncState.SYNCED
        else -> UiSyncState.UNKNOWN
    }
}

fun entitySyncLabel(state: UiSyncState): String = when (state) {
    UiSyncState.SYNCED -> "동기화 완료"
    UiSyncState.PENDING -> "PC 전송 대기"
    UiSyncState.CONFLICT -> "동기화 충돌 · 확인 필요"
    UiSyncState.UNKNOWN -> "동기화 상태 확인 필요"
}
