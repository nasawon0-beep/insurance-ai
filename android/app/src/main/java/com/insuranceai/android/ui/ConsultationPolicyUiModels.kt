package com.insuranceai.android.ui

import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.PolicyEntity
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

data class CustomerChoice(val customerId: String, val label: String)

data class ConsultationDraft(
    val customerId: String,
    val title: String?,
    val channel: String?,
    val content: String,
    val followUpAt: String?,
)

data class PolicyBrowseState(
    val customerId: String? = null,
    val showAllPolicies: Boolean = false,
    val selectedPolicyId: String? = null,
) {
    fun selectCustomer(id: String) = copy(customerId = id, showAllPolicies = false, selectedPolicyId = null)
    fun showAll() = copy(customerId = null, showAllPolicies = true, selectedPolicyId = null)
    fun openPolicy(id: String) = copy(selectedPolicyId = id)
    fun closePolicy() = copy(selectedPolicyId = null)
}

fun customerChoice(customer: CustomerEntity): CustomerChoice {
    val distinction = listOfNotNull(
        customer.phone?.takeIf { it.isNotBlank() },
        customer.birthDate?.takeIf { it.isNotBlank() },
        customer.email?.takeIf { it.isNotBlank() },
    ).joinToString(" · ").ifBlank { "내부 구분 ${customer.id.takeLast(4)}" }
    return CustomerChoice(customer.id, "${customer.name} · $distinction")
}

fun filterCustomerChoices(customers: List<CustomerEntity>, query: String): List<CustomerChoice> {
    val term = query.trim()
    return customers.asSequence()
        .filter { customer ->
            term.isBlank() || listOf(customer.name, customer.phone, customer.birthDate, customer.email)
                .filterNotNull()
                .any { it.contains(term, ignoreCase = true) }
        }
        .map(::customerChoice)
        .toList()
}

fun buildConsultationDraft(
    selectedCustomer: CustomerEntity?,
    title: String,
    channel: String,
    content: String,
    followUpAt: String,
): ConsultationDraft? {
    val customer = selectedCustomer ?: return null
    val cleanContent = content.trim()
    if (cleanContent.isEmpty()) return null
    return ConsultationDraft(
        customerId = customer.id,
        title = title.trim().ifEmpty { null },
        channel = channel.trim().ifEmpty { null },
        content = cleanContent,
        followUpAt = followUpAt.trim().ifEmpty { null },
    )
}

fun linkedCustomer(customerId: String, customers: List<CustomerEntity>): CustomerEntity? =
    customers.firstOrNull { it.id == customerId }

fun selectPolicyById(policies: List<PolicyEntity>, policyId: String): PolicyEntity? =
    policies.firstOrNull { it.id == policyId }

fun policiesInScope(policies: List<PolicyEntity>, state: PolicyBrowseState): List<PolicyEntity> {
    val scoped = when {
        state.showAllPolicies -> policies
        state.customerId != null -> policies.filter { it.customerId == state.customerId }
        else -> emptyList()
    }
    return scoped.sortedWith(
        compareByDescending<PolicyEntity> { it.premium != null }
            .thenByDescending { it.premium ?: Int.MIN_VALUE }
            .thenBy { it.productName.orEmpty() }
            .thenBy { it.id }
    )
}

fun selectedPolicyInScope(policies: List<PolicyEntity>, state: PolicyBrowseState): PolicyEntity? =
    policiesInScope(policies, state).firstOrNull { it.id == state.selectedPolicyId }

fun policyCountsByCustomerId(policies: List<PolicyEntity>): Map<String, Int> =
    policies.groupingBy { it.customerId }.eachCount()

fun paymentCycleLabel(value: String?): String {
    val source = value?.trim().orEmpty()
    return when (source.uppercase()) {
        "" -> "미제공"
        "MONTHLY", "MONTH" -> "매월"
        "QUARTERLY", "QUARTER" -> "분기마다"
        "SEMI_ANNUAL", "SEMIANNUAL" -> "반기마다"
        "ANNUAL", "YEARLY", "YEAR" -> "매년"
        "SINGLE", "LUMP_SUM" -> "일시납"
        else -> if (source.matches(Regex("[a-z0-9_]+", RegexOption.IGNORE_CASE))) "기타 ($source)" else source
    }
}

fun policyPeriodLabel(startDate: String?, endDate: String?): String {
    val start = startDate?.takeIf { it.isNotBlank() }?.let(::formatKoreanDateTime)
    val end = endDate?.takeIf { it.isNotBlank() }?.let(::formatKoreanDateTime)
    return when {
        start == null && end == null -> "미제공"
        start == null -> "시작일 미제공 ~ $end"
        end == null -> "$start ~ 종료일 미제공"
        else -> "$start ~ $end"
    }
}

fun formatKoreanDateTime(value: String?, zoneId: ZoneId = ZoneId.systemDefault()): String {
    val source = value?.trim().orEmpty()
    if (source.isEmpty()) return "없음"
    val dateTimeFormatter = DateTimeFormatter.ofPattern("yyyy년 M월 d일 a h:mm", Locale.KOREAN)
        .withZone(zoneId)
    val dateFormatter = DateTimeFormatter.ofPattern("yyyy년 M월 d일", Locale.KOREAN)
    return runCatching { dateTimeFormatter.format(OffsetDateTime.parse(source).toInstant()) }
        .recoverCatching { dateTimeFormatter.format(ZonedDateTime.parse(source).toInstant()) }
        .recoverCatching { dateTimeFormatter.format(Instant.parse(source)) }
        .recoverCatching { dateTimeFormatter.format(LocalDateTime.parse(source).atZone(zoneId).toInstant()) }
        .recoverCatching { dateFormatter.format(LocalDate.parse(source)) }
        .getOrElse { "날짜 확인 필요" }
}
