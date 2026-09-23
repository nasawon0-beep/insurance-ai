package com.insuranceai.android.auth

import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity

class BiometricAuthManager(private val activity: FragmentActivity) {
    fun isBiometricAvailable(): Boolean = BiometricManager.from(activity).canAuthenticate(
        BiometricManager.Authenticators.BIOMETRIC_STRONG or BiometricManager.Authenticators.DEVICE_CREDENTIAL
    ) == BiometricManager.BIOMETRIC_SUCCESS

    fun authenticate(onSuccess: () -> Unit, onError: (String) -> Unit, onFailedAttempt: () -> Unit = {}) {
        val prompt = BiometricPrompt(
            activity,
            ContextCompat.getMainExecutor(activity),
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) = onSuccess()
                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) = onError(errString.toString())
                override fun onAuthenticationFailed() = onFailedAttempt()
            }
        )
        val promptInfo = BiometricPrompt.PromptInfo.Builder()
            .setTitle("Insurance AI 잠금 해제")
            .setSubtitle("생체 인증 또는 기기 잠금으로 인증하세요")
            .setAllowedAuthenticators(
                BiometricManager.Authenticators.BIOMETRIC_STRONG or BiometricManager.Authenticators.DEVICE_CREDENTIAL
            )
            .build()
        prompt.authenticate(promptInfo)
    }
}

class SessionManager(
    private val timeoutMillis: Long = 5 * 60 * 1000L,
    private val now: () -> Long = { System.currentTimeMillis() }
) {
    private var sessionToken: String? = null
    private var lastActivityTime: Long = 0L

    fun startSession(token: String) {
        sessionToken = token
        lastActivityTime = now()
    }

    fun isSessionValid(): Boolean {
        val token = sessionToken ?: return false
        return token.isNotBlank() && now() - lastActivityTime < timeoutMillis
    }

    fun updateActivity() {
        if (sessionToken != null) lastActivityTime = now()
    }

    fun endSession() {
        sessionToken = null
        lastActivityTime = 0L
    }
}
