package com.insuranceai.android

import android.os.Bundle
import android.view.WindowManager
import androidx.activity.compose.setContent
import androidx.fragment.app.FragmentActivity
import com.insuranceai.android.auth.BiometricAuthManager
import com.insuranceai.android.auth.SessionManager
import com.insuranceai.android.ui.InsuranceAiApp
import java.util.UUID

class MainActivity : FragmentActivity() {
    private lateinit var biometricAuthManager: BiometricAuthManager
    private val sessionManager = SessionManager()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)
        biometricAuthManager = BiometricAuthManager(this)
        setContent {
            InsuranceAiApp(
                sessionManager = sessionManager,
                biometricAvailable = biometricAuthManager.isBiometricAvailable(),
                onAuthenticate = { onSuccess, onError ->
                    biometricAuthManager.authenticate(
                        onSuccess = {
                            sessionManager.startSession(UUID.randomUUID().toString())
                            onSuccess()
                        },
                        onError = onError
                    )
                }
            )
        }
    }

    override fun onUserInteraction() {
        super.onUserInteraction()
        sessionManager.updateActivity()
    }
}
