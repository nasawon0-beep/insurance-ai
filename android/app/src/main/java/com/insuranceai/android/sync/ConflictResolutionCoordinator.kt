package com.insuranceai.android.sync

import com.insuranceai.android.data.local.SyncConflictEntity
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex

interface ConflictResolutionActions {
    /** Returns true only after the server confirms resolution and a refresh no longer lists the conflict. */
    suspend fun resolveConflict(conflict: SyncConflictEntity, chooseLocal: Boolean): Boolean
}

data class ConflictResolutionUiState(
    val inFlightIds: Set<String> = emptySet(),
    val statusByConflict: Map<String, String> = emptyMap(),
)

class ConflictResolutionCoordinator(private val actions: ConflictResolutionActions) {
    private val mutex = Mutex()
    private val mutableState = MutableStateFlow(ConflictResolutionUiState())
    val state: StateFlow<ConflictResolutionUiState> = mutableState.asStateFlow()

    suspend fun resolve(
        conflict: SyncConflictEntity,
        chooseLocal: Boolean,
        authenticate: suspend () -> Boolean,
    ) {
        if (!mutex.tryLock()) return
        update(conflict.id, inFlight = true, status = "선택한 값을 PC에 적용 중")
        try {
            val first = runCatching { actions.resolveConflict(conflict, chooseLocal) }
            val result = if (first.exceptionOrNull()?.requiresUserAuthentication() == true) {
                update(conflict.id, inFlight = true, status = "재인증 필요 · 앱 잠금 해제를 진행하세요")
                if (!runCatching { authenticate() }.getOrDefault(false)) {
                    update(conflict.id, inFlight = false, status = "재인증 필요 · 앱 잠금 해제 후 다시 선택하세요")
                    return
                }
                runCatching { actions.resolveConflict(conflict, chooseLocal) }
            } else {
                first
            }

            result.fold(
                onSuccess = { resolved ->
                    update(
                        conflict.id,
                        inFlight = false,
                        status = if (resolved) {
                            "서버 확인 및 재조회 완료"
                        } else {
                            "미해소 · 서버에서 충돌이 남아 있습니다"
                        },
                    )
                },
                onFailure = { error ->
                    val status = if (error is ConflictResolutionUnavailableException) {
                        error.message ?: "이 충돌은 선택한 값으로 해결할 수 없습니다"
                    } else if (error.requiresUserAuthentication()) {
                        "재인증 필요 · 앱 잠금 해제 후 다시 선택하세요"
                    } else {
                        "해결 실패 · ${safeType(error)}"
                    }
                    update(conflict.id, inFlight = false, status = status)
                },
            )
        } finally {
            if (mutableState.value.inFlightIds.contains(conflict.id)) {
                update(conflict.id, inFlight = false, status = mutableState.value.statusByConflict[conflict.id])
            }
            mutex.unlock()
        }
    }

    private fun update(id: String, inFlight: Boolean, status: String?) {
        val current = mutableState.value
        mutableState.value = current.copy(
            inFlightIds = if (inFlight) current.inFlightIds + id else current.inFlightIds - id,
            statusByConflict = if (status == null) current.statusByConflict else current.statusByConflict + (id to status),
        )
    }

    private fun safeType(error: Throwable): String =
        error.javaClass.simpleName.ifBlank { "오류" }
}
