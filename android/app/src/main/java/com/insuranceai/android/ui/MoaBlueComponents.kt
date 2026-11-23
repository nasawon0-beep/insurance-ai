package com.insuranceai.android.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.insuranceai.android.R

/** Presentation only. All values and actions are supplied by existing screen state. */
@Composable
internal fun MoaBrandHeader() {
    Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Image(painterResource(R.drawable.ic_moa_wordmark_blue), contentDescription = null, modifier = Modifier.size(26.dp))
        Text("모아둠", fontSize = 20.sp, fontWeight = FontWeight.ExtraBold, color = MaterialTheme.colorScheme.onSurface)
    }
}

@Composable
internal fun MoaAvatar(name: String, large: Boolean = false) {
    Surface(shape = CircleShape, color = MaterialTheme.colorScheme.primaryContainer) {
        Box(Modifier.size(if (large) 64.dp else 36.dp), contentAlignment = Alignment.Center) {
            Text(name.take(1).ifBlank { "?" }, fontSize = if (large) 30.sp else 18.sp,
                fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary)
        }
    }
}

@Composable
internal fun MoaStatusBadge(status: String?) {
    val dark = MaterialTheme.colorScheme.surface.luminance() < 0.5f
    val active = status == "가입" || status == "ACTIVE"
    val background = if (active) { if (dark) Color(0xFF245D5C) else Color(0xFFC2F5E9) } else MaterialTheme.colorScheme.primaryContainer
    val foreground = if (active) { if (dark) Color(0xFFB7F5E6) else Color(0xFF065F60) } else MaterialTheme.colorScheme.onPrimaryContainer
    Surface(shape = RoundedCornerShape(12.dp), color = background) {
        Text(businessLabel(status ?: "상태 미지정"), Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
            style = MaterialTheme.typography.labelMedium, color = foreground)
    }
}

@Composable
internal fun MoaShortcut(title: String, subtitle: String, icon: ImageVector, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Surface(modifier.clickable(onClick = onClick), shape = RoundedCornerShape(10.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant), color = MaterialTheme.colorScheme.surface) {
        Column(Modifier.heightIn(min = 104.dp).padding(horizontal = 8.dp, vertical = 12.dp),
            horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(24.dp))
            Text(title, style = MaterialTheme.typography.labelLarge, textAlign = TextAlign.Center)
            Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1, overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center)
        }
    }
}
