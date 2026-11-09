package com.insuranceai.android

import android.app.AlertDialog

import android.content.res.Configuration
import android.graphics.Color
import android.net.Uri
import android.os.Bundle

import android.view.WindowManager
import android.view.Window
import androidx.activity.compose.setContent
import androidx.core.view.WindowCompat
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.lifecycleScope
import com.insuranceai.android.auth.BiometricAuthManager
import com.insuranceai.android.auth.SessionManager

import com.insuranceai.android.ui.InsuranceAiApp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.util.UUID

class MainActivity : FragmentActivity() {
    private lateinit var biometricAuthManager: BiometricAuthManager
    private val sessionManager = SessionManager()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (shouldProtectScreen(BuildConfig.DEBUG)) {
            window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
        }
        WindowCompat.setDecorFitsSystemWindows(window, false)
        window.statusBarColor = Color.TRANSPARENT
        window.navigationBarColor = Color.TRANSPARENT
        val darkMode = resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK ==
            Configuration.UI_MODE_NIGHT_YES
        applySystemBarAppearance(window, useDarkIcons = shouldUseDarkSystemBarIcons(if (darkMode) 0.08f else 0.96f))
        biometricAuthManager = BiometricAuthManager(this)

        // 앱 시작 5초 후 확인하며, 사용자 동의 시 브라우저 다운로드와 Android 설치 승인을 안내합니다.
        lifecycleScope.launch {
            delay(5000)
            checkForUpdates()
        }

        setContent {
            val container = (application as InsuranceAiApplication).container
            InsuranceAiApp(
                sessionManager = sessionManager,
                authenticationGate = container.authenticationGate,
                biometricAvailable = biometricAuthManager.isBiometricAvailable(),
                onAuthenticate = { onSuccess, onError ->
                    biometricAuthManager.authenticate(
                        onSuccess = {
                            sessionManager.startSession(UUID.randomUUID().toString())
                            container.authenticationGate.onAuthenticationSucceeded()
                            lifecycleScope.launch {
                                container.foregroundSyncLifecycleCoordinator.resume(
                                    sessionValid = sessionManager.isSessionValid(),
                                    connectionConfigured = container.syncSettings.isReadyForAutomaticSync(),
                                )
                            }
                            onSuccess()
                        },
                        onError = { error ->
                            container.authenticationGate.onAuthenticationCancelled()
                            onError(error)
                        }
                    )
                }
            )
        }
    }

    private suspend fun checkForUpdates() {
        val updateChecker = UpdateChecker(this)
        val update = updateChecker.checkForUpdate()

        if (update != null) {
            showUpdateDialog(update)
        }
    }

    private fun showUpdateDialog(update: UpdateInfo) {
        AlertDialog.Builder(this)
            .setTitle("업데이트 가능")
            .setMessage("새 버전 v${update.version}을 사용할 수 있습니다.\n\n${update.changelog}")
            .setPositiveButton("업데이트") { _, _ ->
                downloadUpdate(update.downloadUrl)
            }
            .setNegativeButton("나중에", null)
            .setCancelable(!update.required)
            .show()
    }

    private fun downloadUpdate(url: String) {
        if (!UpdateMetadataPolicy.allowedDownload(url)) return
        try {
            // Browser handles GitHub's HTTPS asset redirect and download notification.
            // Opening the APK requires Android's explicit install approval; no silent install.
            startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (_: android.content.ActivityNotFoundException) {
            AlertDialog.Builder(this)
                .setTitle("다운로드 앱이 없습니다")
                .setMessage("브라우저를 활성화한 뒤 다시 시도하세요. 기존 모아둠 앱을 삭제하지 마세요.")
                .setPositiveButton("확인", null).show()
        }
    }

    override fun onUserInteraction() {
        super.onUserInteraction()
        sessionManager.updateActivity()
    }

    override fun onResume() {
        super.onResume()
        val container = (application as InsuranceAiApplication).container
        lifecycleScope.launch {
            container.foregroundSyncLifecycleCoordinator.resume(
                sessionValid = sessionManager.isSessionValid(),
                connectionConfigured = container.syncSettings.isReadyForAutomaticSync(),
            )
        }
    }
}

fun shouldProtectScreen(isDebug: Boolean): Boolean = !isDebug

fun shouldUseDarkSystemBarIcons(appSurfaceLuminance: Float): Boolean = appSurfaceLuminance >= 0.5f

fun applySystemBarAppearance(window: Window, useDarkIcons: Boolean) {
    WindowCompat.getInsetsController(window, window.decorView).apply {
        isAppearanceLightStatusBars = useDarkIcons
        isAppearanceLightNavigationBars = useDarkIcons
    }
}
