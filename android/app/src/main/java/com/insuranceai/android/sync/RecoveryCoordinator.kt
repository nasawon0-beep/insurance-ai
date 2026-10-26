package com.insuranceai.android.sync

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import java.net.SocketTimeoutException

data class RecoveryRequest(val code: String)

data class RecoveryUiState(
    val status: String = "PC에서 복구 코드를 받은 뒤 입력하세요",
    val inFlight: Boolean = false,
    val codeConsumed: Boolean = false,
    val recovered: Boolean = false,
)

interface RecoveryActions {
    suspend fun prepareRecoveryStorage()
    suspend fun recoverAndPromote(request: RecoveryRequest)
}

class RecoveryCoordinator(private val actions: RecoveryActions) {
    private val mutex = Mutex()
    private val mutableState = MutableStateFlow(RecoveryUiState())
    val state: StateFlow<RecoveryUiState> = mutableState.asStateFlow()

    suspend fun recover(request: RecoveryRequest, authenticate: suspend () -> Boolean) {
        if (!mutex.tryLock()) return
        try {
            mutableState.value = mutableState.value.copy(status = "로컬 보안 저장소 확인 중", inFlight = true)
            try {
                actions.prepareRecoveryStorage()
            } catch (error: Throwable) {
                if (!error.requiresUserAuthentication()) {
                    update("복구 준비 실패 · 기존 연결 정보와 미전송 자료는 그대로입니다")
                    return
                }
                update("기존 연결 복구 전 기기 인증이 필요합니다", inFlight = true)
                if (!runCatching { authenticate() }.getOrDefault(false)) {
                    update("기기 인증이 취소되어 복구를 시작하지 않았습니다")
                    return
                }
                try {
                    actions.prepareRecoveryStorage()
                } catch (_: Throwable) {
                    update("기기 인증 후에도 복구를 준비할 수 없습니다")
                    return
                }
            }

            update("PC에 기존 연결 복구 요청 중", inFlight = true)
            try {
                actions.recoverAndPromote(request)
            } catch (_: SocketTimeoutException) {
                update("PC 응답 시간 초과 · 기존 연결 정보와 미전송 자료는 그대로입니다. 같은 코드를 다시 시도하세요")
                return
            } catch (error: LanSyncHttpException) {
                update(if (error.statusCode == 401 || error.statusCode == 429) {
                    "복구 코드를 확인하거나 잠시 후 다시 시도하세요 · 기존 자료는 그대로입니다"
                } else {
                    "기존 연결 복구 실패 · 기존 자료는 그대로입니다"
                })
                return
            } catch (_: Throwable) {
                update("기존 연결 복구 실패 · 기존 연결 정보와 미전송 자료는 그대로입니다")
                return
            }
            update(
                status = "기존 연결 복구 완료 · 미전송 자료를 유지했습니다",
                codeConsumed = true,
                recovered = true,
            )
        } finally {
            mutableState.value = mutableState.value.copy(inFlight = false)
            mutex.unlock()
        }
    }

    private fun update(
        status: String,
        inFlight: Boolean = mutableState.value.inFlight,
        codeConsumed: Boolean = mutableState.value.codeConsumed,
        recovered: Boolean = mutableState.value.recovered,
    ) {
        mutableState.value = RecoveryUiState(status, inFlight, codeConsumed, recovered)
    }
}
