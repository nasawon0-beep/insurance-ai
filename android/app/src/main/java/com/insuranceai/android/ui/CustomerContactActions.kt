package com.insuranceai.android.ui

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Call
import androidx.compose.material.icons.outlined.Sms
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

/** Test-only dispatch seam: null uses the real user-controlled Android application. */
internal val LocalCustomerContactLauncher = staticCompositionLocalOf<((Intent) -> Unit)?> { null }

/** Only opens user-controlled apps. No phone/SMS permission or automatic action. */
internal fun openCustomerContact(raw: String?, action: ContactAction, launch: (Intent) -> Unit): String? {
    val target = customerContactTarget(raw) ?: return "연락처를 확인해 주세요."
    val intent = Intent(action.androidAction, Uri.fromParts(action.scheme, target.destination, null))
    return try {
        launch(intent)
        null
    } catch (_: ActivityNotFoundException) {
        "이 작업을 처리할 앱이 없습니다."
    } catch (_: SecurityException) {
        "기기 설정으로 앱을 열 수 없습니다."
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun CustomerContactActions(phone: String?) {
    val context = LocalContext.current
    val launchOverride = LocalCustomerContactLauncher.current
    var message by remember(phone) { mutableStateOf<String?>(null) }
    val available = customerContactTarget(phone) != null
    Column {
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(phone?.takeIf { it.isNotBlank() } ?: "연락처 없음", style = MaterialTheme.typography.bodyMedium)
            ContactAction.entries.forEach { action ->
                TextButton(
                    modifier = Modifier.heightIn(min = 48.dp),
                    enabled = available,
                    onClick = { message = openCustomerContact(phone, action) { launchOverride?.invoke(it) ?: context.startActivity(it) } },
                ) {
                    Icon(if (action == ContactAction.DIAL) Icons.Outlined.Call else Icons.Outlined.Sms, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text(if (action == ContactAction.DIAL) "전화하기" else "문자하기")
                }
            }
        }
        if (!available && !phone.isNullOrBlank()) Text("연락처 형식을 확인해 주세요.", color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
        message?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
    }
}
