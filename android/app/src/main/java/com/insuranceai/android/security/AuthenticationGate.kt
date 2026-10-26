package com.insuranceai.android.security

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Explicit lock state for Android Keystore authentication expiry. */
data class AuthenticationState(
    val required: Boolean = false,
    val requestId: Long = 0,
    val resumeId: Long = 0,
)

class AuthenticationRequiredException(cause: Throwable? = null) :
    SecurityException("User authentication is required", cause)

fun Throwable.isUserAuthenticationRequired(): Boolean =
    generateSequence(this) { it.cause }.any { cause ->
        cause is AuthenticationRequiredException ||
            cause.javaClass.simpleName == "UserNotAuthenticatedException"
    }

class AuthenticationGate {
    private val mutableState = MutableStateFlow(AuthenticationState())
    val state: StateFlow<AuthenticationState> = mutableState.asStateFlow()

    fun <T> runSensitive(block: () -> T): T = try {
        block()
    } catch (error: Throwable) {
        if (!error.isUserAuthenticationRequired()) throw error
        val current = mutableState.value
        if (!current.required) {
            mutableState.value = current.copy(required = true, requestId = current.requestId + 1)
        }
        if (error is AuthenticationRequiredException) throw error
        throw AuthenticationRequiredException(error)
    }

    fun <T> readOrNull(block: () -> T): T? = try {
        runSensitive(block)
    } catch (_: AuthenticationRequiredException) {
        null
    }

    fun onAuthenticationSucceeded() {
        val current = mutableState.value
        if (current.required) {
            mutableState.value = current.copy(required = false, resumeId = current.resumeId + 1)
        }
    }

    fun onAuthenticationCancelled() {
        // Intentionally remains locked. Existing keys and encrypted data are untouched.
    }
}
