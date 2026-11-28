package com.insuranceai.android.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Assessment
import androidx.compose.material.icons.outlined.ChevronRight
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.insuranceai.android.data.local.CustomerEntity

/** Approved blue UI tokens shared by real production screens; no preview state. */
@Composable
internal fun MoaTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val colors = if (dark) darkColorScheme(
        primary = Color(0xFF6F9EFF), onPrimary = Color(0xFF08163C),
        primaryContainer = Color(0xFF1E3153), onPrimaryContainer = Color(0xFFE5EDFF),
        secondary = Color(0xFF6F9EFF), onSecondary = Color(0xFF08163C),
        secondaryContainer = Color(0xFF1E3153), onSecondaryContainer = Color(0xFFE5EDFF),
        background = Color(0xFF111E34), onBackground = Color(0xFFE5EDFF),
        surface = Color(0xFF111E34), onSurface = Color(0xFFE5EDFF),
        surfaceVariant = Color(0xFF18263D), onSurfaceVariant = Color(0xFFA6B3CD),
        outline = Color(0xFF8896B3), outlineVariant = Color(0xFF2E3E59),
        error = Color(0xFFFFB4AB), onError = Color(0xFF4A2A2F),
        errorContainer = Color(0xFF4A2A2F), onErrorContainer = Color(0xFFFFB4AB),
    ) else lightColorScheme(
        primary = Color(0xFF0055FF), onPrimary = Color.White,
        primaryContainer = Color(0xFFEDF4FF), onPrimaryContainer = Color(0xFF0055FF),
        secondary = Color(0xFF0055FF), onSecondary = Color.White,
        secondaryContainer = Color(0xFFEDF4FF), onSecondaryContainer = Color(0xFF0055FF),
        background = Color.White, onBackground = Color(0xFF08163C),
        surface = Color.White, onSurface = Color(0xFF08163C),
        surfaceVariant = Color(0xFFF7F9FC), onSurfaceVariant = Color(0xFF5B6883),
        outline = Color(0xFF6E7B96), outlineVariant = Color(0xFFE4E9F2),
        error = Color(0xFFB3261E), onError = Color.White,
        errorContainer = Color(0xFFFDECEA), onErrorContainer = Color(0xFFB3261E),
    )
    MaterialTheme(
        colorScheme = colors,
        shapes = Shapes(small = RoundedCornerShape(8.dp), medium = RoundedCornerShape(10.dp), large = RoundedCornerShape(10.dp)),
        typography = Typography(
            headlineSmall = TextStyle(fontSize = 25.sp, lineHeight = 34.sp, fontWeight = FontWeight.Bold),
            titleLarge = TextStyle(fontSize = 21.sp, lineHeight = 30.sp, fontWeight = FontWeight.Bold),
            titleMedium = TextStyle(fontSize = 16.sp, lineHeight = 24.sp, fontWeight = FontWeight.Bold),
            bodyLarge = TextStyle(fontSize = 14.sp, lineHeight = 22.sp),
            bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 22.sp),
            bodySmall = TextStyle(fontSize = 13.sp, lineHeight = 21.sp),
            labelLarge = TextStyle(fontSize = 14.sp, lineHeight = 22.sp, fontWeight = FontWeight.SemiBold),
            labelMedium = TextStyle(fontSize = 12.sp, lineHeight = 18.sp, fontWeight = FontWeight.SemiBold),
        ), content = content,
    )
}

@Composable
internal fun MoaTopBar(title: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 12.dp)) {
        ProvideTextStyle(MaterialTheme.typography.headlineSmall) { title() }
    }
}

@Composable
internal fun MoaSearchField(value: String, onValueChange: (String) -> Unit, label: @Composable () -> Unit, modifier: Modifier = Modifier) {
    OutlinedTextField(value, onValueChange, label = label, modifier = modifier,
        leadingIcon = { Icon(Icons.Outlined.Search, null, Modifier.size(20.dp)) },
        shape = RoundedCornerShape(10.dp),
        colors = OutlinedTextFieldDefaults.colors(
            unfocusedContainerColor = MaterialTheme.colorScheme.surface,
            focusedContainerColor = MaterialTheme.colorScheme.surface,
            unfocusedBorderColor = MaterialTheme.colorScheme.outlineVariant,
        ),
    )
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun MoaCustomerSummary(customer: CustomerEntity, onEdit: () -> Unit) {
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            MoaAvatar(customer.name, large = true)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(customer.name, style = MaterialTheme.typography.titleLarge)
                MoaStatusBadge(customer.customerStatus)
            }
            OutlinedButton(onClick = onEdit, modifier = Modifier.heightIn(min = 48.dp),
                shape = RoundedCornerShape(8.dp), contentPadding = PaddingValues(horizontal = 10.dp, vertical = 8.dp)) { Text("정보 수정") }
        }
        CustomerContactActions(customer.phone)
    }
}

@Composable
internal fun MoaAnalysisRecord(customer: String, date: String, summary: String, onOpen: () -> Unit) {
    Column {
        MoaBusinessRow(customer, "$date\n$summary", androidx.compose.material.icons.Icons.Outlined.Assessment, onOpen)
        Spacer(Modifier.height(8.dp))
    }
}

@Composable
internal fun MoaSyncStatusPanel(status: String, registered: Boolean, conflictCount: Int, enabled: Boolean, onSync: () -> Unit) {
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("동기화 상태", style = MaterialTheme.typography.titleMedium)
        Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.primaryContainer) {
            Text(status, Modifier.fillMaxWidth().padding(16.dp), color = MaterialTheme.colorScheme.onPrimaryContainer)
        }
        Button(onClick = onSync, enabled = enabled, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) { Text("지금 동기화") }
        Text("자동 동기화는 네트워크 연결 후 OS가 실행하며, 실행 시각은 보장되지 않습니다.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (!registered) Text("PC를 처음 등록하면 이후에는 새 코드 없이 동기화합니다.", style = MaterialTheme.typography.bodySmall)
        if (conflictCount > 0) Text("충돌 ${conflictCount}건 · 자동 덮어쓰기하지 않습니다.", color = MaterialTheme.colorScheme.error, fontWeight = FontWeight.Bold)
    }
}

@Composable
internal fun MoaCard(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Card(modifier = modifier, shape = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        elevation = CardDefaults.cardElevation(defaultElevation = 0.dp), content = content)
}

@Composable
internal fun MoaBusinessRow(title: String, subtitle: String, icon: ImageVector, onClick: () -> Unit) {
    MoaCard(Modifier.fillMaxWidth()) {
    Row(Modifier.fillMaxWidth().heightIn(min = 72.dp).clickable(onClick = onClick).padding(12.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Surface(shape = RoundedCornerShape(10.dp), color = MaterialTheme.colorScheme.primaryContainer) {
            Box(Modifier.size(32.dp), contentAlignment = Alignment.Center) { Icon(icon, null, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.primary) }
        }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(title, fontWeight = FontWeight.Bold)
            Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Icon(Icons.Outlined.ChevronRight, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    }
}
