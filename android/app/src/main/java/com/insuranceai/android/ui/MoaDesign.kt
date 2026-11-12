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

/** Shared production theme matching approved b-refined, not a preview theme. */
@Composable
internal fun MoaTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val colors = if (dark) darkColorScheme(
        primary = Color(0xFF9FC5FF), onPrimary = Color(0xFF142B49),
        primaryContainer = Color(0xFF243B59), onPrimaryContainer = Color(0xFF9FC5FF),
        background = Color(0xFF131820), onBackground = Color(0xFFF5F7FC),
        surface = Color(0xFF1C2430), onSurface = Color(0xFFF5F7FC),
        surfaceVariant = Color(0xFF20334D), onSurfaceVariant = Color(0xFFB7C1D0),
        outline = Color(0xFF526074), outlineVariant = Color(0xFF394455),
    ) else lightColorScheme(
        primary = Color(0xFF155CCE), onPrimary = Color.White,
        primaryContainer = Color(0xFFEDF4FF), onPrimaryContainer = Color(0xFF155CCE),
        background = Color.White, onBackground = Color(0xFF171B24),
        surface = Color.White, onSurface = Color(0xFF171B24),
        surfaceVariant = Color(0xFFEEF4FD), onSurfaceVariant = Color(0xFF5D6676),
        outline = Color(0xFF788597), outlineVariant = Color(0xFFE3E8EF),
    )
    MaterialTheme(
        colorScheme = colors,
        shapes = Shapes(small = RoundedCornerShape(12.dp), medium = RoundedCornerShape(16.dp), large = RoundedCornerShape(18.dp)),
        typography = Typography(
            headlineSmall = TextStyle(fontSize = 26.sp, lineHeight = 36.sp, fontWeight = FontWeight.ExtraBold),
            titleLarge = TextStyle(fontSize = 24.sp, lineHeight = 34.sp, fontWeight = FontWeight.Bold),
            titleMedium = TextStyle(fontSize = 18.sp, lineHeight = 27.sp, fontWeight = FontWeight.Bold),
            bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 25.sp),
            bodyMedium = TextStyle(fontSize = 16.sp, lineHeight = 25.sp),
            bodySmall = TextStyle(fontSize = 14.sp, lineHeight = 22.sp),
            labelLarge = TextStyle(fontSize = 14.sp, lineHeight = 22.sp, fontWeight = FontWeight.SemiBold),
            labelMedium = TextStyle(fontSize = 13.sp, lineHeight = 20.sp, fontWeight = FontWeight.SemiBold),
        ), content = content,
    )
}

@Composable
internal fun MoaTopBar(title: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 22.dp, vertical = 18.dp)) {
        Text("모아둠", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(6.dp))
        ProvideTextStyle(MaterialTheme.typography.headlineSmall) { title() }
    }
}

@Composable
internal fun MoaSearchField(value: String, onValueChange: (String) -> Unit, label: @Composable () -> Unit, modifier: Modifier = Modifier) {
    OutlinedTextField(value, onValueChange, label = label, modifier = modifier,
        leadingIcon = { Icon(Icons.Outlined.Search, null, Modifier.size(20.dp)) },
        shape = RoundedCornerShape(12.dp),
        colors = OutlinedTextFieldDefaults.colors(
            unfocusedContainerColor = MaterialTheme.colorScheme.primaryContainer,
            focusedContainerColor = MaterialTheme.colorScheme.primaryContainer,
            unfocusedBorderColor = Color.Transparent,
        ),
    )
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun MoaCustomerSummary(customer: CustomerEntity, onEdit: () -> Unit) {
    Surface(shape = RoundedCornerShape(18.dp), color = MaterialTheme.colorScheme.surfaceVariant,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant), shadowElevation = 1.dp) {
        Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(businessLabel(customer.customerStatus ?: "상태 미지정"), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(customer.name, style = MaterialTheme.typography.titleLarge)
                OutlinedButton(onClick = onEdit, modifier = Modifier.heightIn(min = 48.dp)) { Text("정보 수정") }
            }
            CustomerContactActions(customer.phone)
        }
    }
}

@Composable
internal fun MoaAnalysisRecord(customer: String, date: String, summary: String, onOpen: () -> Unit) {
    Column {
        MoaBusinessRow(customer, "$date\n$summary", androidx.compose.material.icons.Icons.Outlined.Assessment, onOpen)
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
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
    Card(modifier = modifier, shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp), content = content)
}

@Composable
internal fun MoaBusinessRow(title: String, subtitle: String, icon: ImageVector, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 72.dp).clickable(onClick = onClick).padding(vertical = 16.dp),
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
