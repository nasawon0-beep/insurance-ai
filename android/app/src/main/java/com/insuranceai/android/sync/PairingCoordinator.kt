package com.insuranceai.android.sync

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex

/** Inputs are kept in memory only and are never included in status/error text. */
data class PairingRequest(
    val endpoint: String,
    val certificatePin: String,
    val code: String,
    val deviceName: String,
)

data class PairingUiState(
    val status: String = "페어링 필요 또는 동기화 대기",
    val inFlight: Boolean = false,
    val codeConsumed: Boolean = false,
    val requiresNewCode: Boolean = false,
    val paired: Boolean = false,
)

interface PairingActions {
    suspend fun prepareCredentialStorage()
    suspend fun pairAndSave(request: PairingRequest)
    suspend fun syncOnce(): Boolean
}

/** The server accepted the one-time code, but the returned credential was not saved locally. */
class PairingLocalSaveException(cause: Throwable) : IllegalStateException(
    "paired server credential could not be saved locally",
    cause,
)

class PairingCoordinator(private val actions: PairingActions) {
    private val mutex = Mutex()
    private val mutableState = MutableStateFlow(PairingUiState())
    val state: StateFlow<PairingUiState> = mutableState.asStateFlow()
    private var consumedCode: String? = null

    suspend fun pair(request: PairingRequest, authenticate: suspend () -> Boolean) {
        if (!mutex.tryLock()) return
        try {
            if (consumedCode != null && request.code == consumedCode) {
                mutableState.value = mutableState.value.copy(
                    codeConsumed = true,
                    requiresNewCode = true,
                )
                return
            }
            update(status = "로컬 보안 저장소 확인 중", inFlight = true)

            try {
                actions.prepareCredentialStorage()
            } catch (error: Throwable) {
                if (!error.requiresUserAuthentication()) {
                    update(status = "로컬 보안 저장소 확인 실패 (${safeType(error)})")
                    return
                }
                update(status = "페어링 전 기기 인증이 필요합니다")
                val authenticated = runCatching { authenticate() }.getOrDefault(false)
                if (!authenticated) {
                    update(status = "기기 인증이 취소되어 페어링을 시작하지 않았습니다")
                    return
                }
                try {
                    actions.prepareCredentialStorage()
                } catch (retryError: Throwable) {
                    update(status = "기기 인증 후에도 로컬 보안 저장소를 사용할 수 없습니다 (${safeType(retryError)})")
                    return
                }
            }

            update(status = "PC에 페어링 요청 중")
            try {
                actions.pairAndSave(request)
            } catch (error: PairingLocalSaveException) {
                consumedCode = request.code
                update(
                    status = "PC 페어링은 완료됐지만 인증정보를 기기에 저장하지 못했습니다 (${safeType(error.cause ?: error)}). 같은 코드를 다시 보내지 마세요. 기기 인증 후 PC에서 새 일회용 코드를 받아 다시 시도하세요.",
                    codeConsumed = true,
                    requiresNewCode = true,
                    paired = false,
                )
                return
            } catch (error: Throwable) {
                update(status = "PC 페어링 요청 실패 (${safeType(error)}). 주소·인증서 지문·새 일회용 코드를 확인하세요")
                return
            }

            consumedCode = request.code
            update(
                status = "페어링 정보 저장 완료 · 최초 동기화 중",
                codeConsumed = true,
                requiresNewCode = false,
                paired = true,
            )
            try {
                if (actions.syncOnce()) {
                    update(status = "페어링 정보 저장 및 최초 동기화 완료")
                } else {
                    update(status = "페어링 정보 저장 완료 · 최초 동기화 대기. 지금 동기화를 다시 시도하세요")
                }
            } catch (error: Throwable) {
                update(status = "페어링 정보 저장 완료 · 최초 동기화 실패 (${safeType(error)}). 지금 동기화를 다시 시도하세요")
            }
        } finally {
            mutableState.value = mutableState.value.copy(inFlight = false)
            mutex.unlock()
        }
    }

    fun onCodeChanged(newCode: String) {
        val oldCode = consumedCode ?: return
        if (mutableState.value.requiresNewCode && newCode.isNotBlank() && newCode != oldCode) {
            mutableState.value = mutableState.value.copy(
                requiresNewCode = false,
                codeConsumed = false,
                paired = false,
            )
        }
    }

    private fun update(
        status: String,
        inFlight: Boolean = mutableState.value.inFlight,
        codeConsumed: Boolean = mutableState.value.codeConsumed,
        requiresNewCode: Boolean = mutableState.value.requiresNewCode,
        paired: Boolean = mutableState.value.paired,
    ) {
        mutableState.value = PairingUiState(status, inFlight, codeConsumed, requiresNewCode, paired)
    }

    private fun safeType(error: Throwable): String = error.javaClass.simpleName.ifBlank { "오류" }
}
