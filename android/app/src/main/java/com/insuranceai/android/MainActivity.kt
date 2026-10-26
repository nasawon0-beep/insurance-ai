package com.insuranceai.android

import android.app.AlertDialog
import android.app.DownloadManager
import android.content.Context
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Environment
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
        applySystemBarAppearance(window, useDarkIcons = shouldUseDarkSystemBarIcons(0.96f))
        biometricAuthManager = BiometricAuthManager(this)

        // 앱 시작 5초 후 업데이트 API를 확인합니다. 업데이트가 있으면 DownloadManager로 APK를 내려받습니다.
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
                                    connectionConfigured = container.syncSettings.isConfigured(),
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
        val request = DownloadManager.Request(Uri.parse(url))
            .setTitle("insurance-ai 업데이트")
            .setDescription("새 버전 다운로드 중...")
            .setDestinationInExternalFilesDir(
                this,
                Environment.DIRECTORY_DOWNLOADS,
                "update.apk"
            )
            .setNotificationVisibility(
                DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED
            )

        val downloadManager = getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        downloadManager.enqueue(request)
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
                connectionConfigured = container.syncSettings.isConfigured(),
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
