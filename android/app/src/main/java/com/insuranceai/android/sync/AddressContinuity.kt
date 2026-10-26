package com.insuranceai.android.sync

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex

data class AddressContinuityRequest(
    val endpoint: String,
    val certificatePin: String,
    val certificateConfirmedFromPc: Boolean,
)

data class ContinuityProof(
    val status: String,
    val deviceId: String,
    val unresolvedConflictIds: Set<String>,
    val pcInstanceId: String? = null,
)

fun validateContinuityProof(
    expectedDeviceId: String?,
    localConflictIds: Set<String>,
    proof: ContinuityProof,
) {
    require(proof.status == "authenticated-device") { "Invalid continuity status" }
    require(proof.deviceId.isNotBlank()) { "Missing device identity" }
    if (expectedDeviceId != null) {
        require(proof.deviceId == expectedDeviceId) { "Device identity changed" }
    }
    require(proof.unresolvedConflictIds.containsAll(localConflictIds)) {
        "Unresolved conflict ownership changed"
    }
}

interface AddressContinuityActions {
    suspend fun verifyAndUpdateAddress(request: AddressContinuityRequest)
}

data class AddressContinuityUiState(
    val inFlight: Boolean = false,
    val status: String = "주소가 바뀐 경우에만 PC 화면의 새 주소와 인증서 SHA-256을 확인하세요",
)

class AddressContinuityCoordinator(private val actions: AddressContinuityActions) {
    private val mutex = Mutex()
    private val mutableState = MutableStateFlow(AddressContinuityUiState())
    val state: StateFlow<AddressContinuityUiState> = mutableState.asStateFlow()

    suspend fun update(request: AddressContinuityRequest, authenticate: suspend () -> Boolean) {
        if (!mutex.tryLock()) return
        try {
            if (!request.certificateConfirmedFromPc) {
                mutableState.value = AddressContinuityUiState(status = "PC 화면의 새 인증서 SHA-256 일치를 먼저 확인하세요")
                return
            }
            mutableState.value = AddressContinuityUiState(inFlight = true, status = "기존 기기 identity와 미해결 충돌을 확인 중")
            val first = runCatching { actions.verifyAndUpdateAddress(request) }
            val result = if (first.exceptionOrNull()?.requiresUserAuthentication() == true) {
                mutableState.value = AddressContinuityUiState(inFlight = true, status = "재인증 필요 · 앱 잠금 해제를 진행하세요")
                if (!runCatching { authenticate() }.getOrDefault(false)) {
                    mutableState.value = AddressContinuityUiState(status = "재인증이 취소되어 기존 연결 정보를 유지했습니다")
                    return
                }
                runCatching { actions.verifyAndUpdateAddress(request) }
            } else {
                first
            }
            mutableState.value = result.fold(
                onSuccess = { AddressContinuityUiState(status = "같은 PC·기기 identity·미해결 충돌 확인 완료 · 연결 주소 갱신됨") },
                onFailure = { error ->
                    val status = if (error.requiresUserAuthentication()) {
                        "재인증 필요 · 기존 연결 정보를 유지했습니다"
                    } else {
                        "주소 갱신 실패 · 기존 연결 정보를 유지했습니다 (${safeType(error)})"
                    }
                    AddressContinuityUiState(status = status)
                },
            )
        } finally {
            mutableState.value = mutableState.value.copy(inFlight = false)
            mutex.unlock()
        }
    }

    private fun safeType(error: Throwable): String = error.javaClass.simpleName.ifBlank { "오류" }
}
