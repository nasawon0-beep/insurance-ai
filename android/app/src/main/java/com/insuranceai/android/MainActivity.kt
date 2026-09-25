package com.insuranceai.android

import android.app.AlertDialog
import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.view.WindowManager
import androidx.activity.compose.setContent
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
        window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)
        biometricAuthManager = BiometricAuthManager(this)

        // 앱 시작 5초 후 업데이트 API를 확인합니다. 업데이트가 있으면 DownloadManager로 APK를 내려받습니다.
        lifecycleScope.launch {
            delay(5000)
            checkForUpdates()
        }

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
}
