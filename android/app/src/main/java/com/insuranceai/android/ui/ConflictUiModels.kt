package com.insuranceai.android.ui

import com.insuranceai.android.data.local.SyncConflictEntity
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

data class ConflictFieldUi(
    val label: String,
    val mobileValue: String?,
    val pcValue: String?,
)

data class ConflictCardUi(
    val customerName: String?,
    val fields: List<ConflictFieldUi>,
    val remoteMissing: Boolean = false,
)

class MissingCustomerDeleteConfirmationCoordinator {
    private val mutableTarget = MutableStateFlow<SyncConflictEntity?>(null)
    val target: StateFlow<SyncConflictEntity?> = mutableTarget.asStateFlow()

    fun request(conflict: SyncConflictEntity, remoteMissing: Boolean): Boolean {
        if (mutableTarget.value != null || !remoteMissing || conflict.entityType != "customer") return false
        mutableTarget.value = conflict
        return true
    }

    fun dismiss() {
        mutableTarget.value = null
    }

    fun confirm(
        currentConflicts: List<SyncConflictEntity>,
        resolve: (SyncConflictEntity) -> Unit,
    ): Boolean {
        val requested = mutableTarget.value ?: return false
        mutableTarget.value = null
        val unchanged = currentConflicts.firstOrNull { it == requested } ?: return false
        resolve(unchanged)
        return true
    }
}

fun customerConflictCardUi(
    base: Map<String, Any?>,
    local: Map<String, Any?>,
    remote: Map<String, Any?>,
): ConflictCardUi {
    val effectiveLocal = base + local
    val labels = linkedMapOf(
        "phone" to "연락처",
        "address" to "주소",
        "memo" to "메모",
    )
    val fields = labels.mapNotNull { (key, label) ->
        if (!local.containsKey(key) || effectiveLocal[key] == remote[key]) return@mapNotNull null
        ConflictFieldUi(
            label = label,
            mobileValue = effectiveLocal[key]?.toString(),
            pcValue = remote[key]?.toString(),
        )
    }
    val customerName = sequenceOf(remote["name"], local["name"], base["name"])
        .filterNotNull()
        .map(Any::toString)
        .firstOrNull { it.isNotBlank() }
    return ConflictCardUi(customerName = customerName, fields = fields, remoteMissing = remote.isEmpty())
}
