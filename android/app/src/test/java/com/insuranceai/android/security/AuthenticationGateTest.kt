package com.insuranceai.android.security

import com.insuranceai.android.sync.requiresUserAuthentication
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

private class UserNotAuthenticatedException(message: String = "expired") : Exception(message)

class AuthenticationGateTest {
    @Test
    fun authenticationExpiryLocksAndRethrowsAsDomainError() {
        val gate = AuthenticationGate()

        assertThrows(AuthenticationRequiredException::class.java) {
            gate.runSensitive<String> { throw UserNotAuthenticatedException() }
        }

        assertTrue(gate.state.value.required)
        assertTrue(gate.state.value.requestId > 0)
    }

    @Test
    fun successfulAuthenticationUnlocksWithoutReplacingKeysOrData() {
        val gate = AuthenticationGate()
        runCatching { gate.runSensitive<String> { throw UserNotAuthenticatedException() } }
        val requestId = gate.state.value.requestId

        gate.onAuthenticationSucceeded()

        assertFalse(gate.state.value.required)
        assertTrue(gate.state.value.resumeId > 0)
        assertTrue(gate.state.value.requestId == requestId)
    }

    @Test
    fun cancellationKeepsLockedAndDoesNotAdvanceResume() {
        val gate = AuthenticationGate()
        runCatching { gate.runSensitive<String> { throw UserNotAuthenticatedException() } }

        gate.onAuthenticationCancelled()

        assertTrue(gate.state.value.required)
        assertTrue(gate.state.value.resumeId == 0L)
    }

    @Test
    fun unrelatedSecurityExceptionIsNotMisclassifiedOrSwallowed() {
        val gate = AuthenticationGate()
        val error = SecurityException("certificate pin mismatch")

        val thrown = assertThrows(SecurityException::class.java) {
            gate.runSensitive<String> { throw error }
        }

        assertTrue(thrown === error)
        assertFalse(gate.state.value.required)
        assertFalse(error.requiresUserAuthentication())
    }

    @Test
    fun authenticationExceptionIsRecognizedThroughCauseChain() {
        val error = IllegalStateException("wrapper", UserNotAuthenticatedException())
        assertTrue(error.requiresUserAuthentication())
    }

    @Test
    fun uiReadReturnsLockedMarkerInsteadOfCrashingMainThread() {
        val gate = AuthenticationGate()

        val value = gate.readOrNull<String> { throw UserNotAuthenticatedException() }

        assertTrue(value == null)
        assertTrue(gate.state.value.required)
    }
}
