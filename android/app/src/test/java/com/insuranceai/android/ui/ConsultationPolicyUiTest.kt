package com.insuranceai.android.ui

import com.insuranceai.android.data.local.ConsultationEntity
import com.insuranceai.android.data.local.CustomerEntity
import com.insuranceai.android.data.local.PolicyEntity
import java.time.ZoneId
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ConsultationPolicyUiTest {
    private val firstKim = CustomerEntity(
        id = "customer-1",
        name = "합성동명",
        phone = "010-0000-0001",
        birthDate = "1980-01-02",
    )
    private val secondKim = CustomerEntity(
        id = "customer-2",
        name = "합성동명",
        phone = "010-0000-0002",
        birthDate = "1990-03-04",
    )

    @Test
    fun customerChoiceUsesExactIdWhileDuplicateNamesAreDistinguishable() {
        val first = customerChoice(firstKim)
        val second = customerChoice(secondKim)

        assertEquals("customer-1", first.customerId)
        assertEquals("customer-2", second.customerId)
        assertFalse(first.label.contains("customer-1"))
        assertFalse(second.label.contains("customer-2"))
        assertFalse(first.label == second.label)
        assertTrue(first.label.contains("010-0000-0001"))
        assertTrue(second.label.contains("1990-03-04"))
    }

    @Test
    fun customerSearchMatchesNamePhoneAndKeepsEntityId() {
        val customers = listOf(firstKim, secondKim)

        assertEquals(listOf("customer-1", "customer-2"), filterCustomerChoices(customers, "합성동명").map { it.customerId })
        assertEquals(listOf("customer-2"), filterCustomerChoices(customers, "0002").map { it.customerId })
    }

    @Test
    fun consultationDraftRejectsMissingCustomerAndEmptyBody() {
        assertNull(buildConsultationDraft(null, "제목", "전화", "내용", ""))
        assertNull(buildConsultationDraft(firstKim, "제목", "전화", "   ", ""))

        val draft = buildConsultationDraft(secondKim, "제목", "전화", "상담 본문", "2026-10-22")
        assertEquals("customer-2", draft?.customerId)
        assertEquals("상담 본문", draft?.content)
    }

    @Test
    fun consultationRelationUsesIdNotDisplayName() {
        val item = ConsultationEntity(id = "consult-1", customerId = "customer-2", consultedAt = "2026-10-21T00:00:00Z")

        assertEquals("customer-2", linkedCustomer(item.customerId, listOf(firstKim, secondKim))?.id)
    }

    @Test
    fun datesConvertTimezoneAndHandleDateOnlyAndInvalidSafely() {
        val seoul = ZoneId.of("Asia/Seoul")

        assertEquals("2026년 10월 21일 오전 9:00", formatKoreanDateTime("2026-10-21T00:00:00+00:00", seoul))
        assertEquals("2026년 10월 22일", formatKoreanDateTime("2026-10-22", seoul))
        assertEquals("날짜 확인 필요", formatKoreanDateTime("not-a-date", seoul))
        assertEquals("없음", formatKoreanDateTime(null, seoul))
    }

    @Test
    fun policySelectionAndCustomerRelationUseExactIds() {
        val first = PolicyEntity(id = "policy-1", customerId = "customer-1", insurer = "A")
        val second = PolicyEntity(id = "policy-2", customerId = "customer-2", insurer = "B")

        assertEquals("policy-2", selectPolicyById(listOf(first, second), "policy-2")?.id)
        assertEquals("customer-2", linkedCustomer(second.customerId, listOf(firstKim, secondKim))?.id)
        assertNull(selectPolicyById(listOf(first, second), "missing"))
    }

    @Test
    fun policyScopeNeverMixesDuplicateNamesAndClearsStaleDetailOnCustomerChange() {
        val firstPolicy = PolicyEntity(id = "policy-1", customerId = "customer-1", insurer = "A")
        val secondPolicy = PolicyEntity(id = "policy-2", customerId = "customer-2", insurer = "B")
        val policies = listOf(firstPolicy, secondPolicy)

        val firstScope = PolicyBrowseState().selectCustomer("customer-1").openPolicy("policy-1")
        assertEquals(listOf("policy-1"), policiesInScope(policies, firstScope).map { it.id })
        assertEquals("policy-1", selectedPolicyInScope(policies, firstScope)?.id)

        val afterBack = firstScope.closePolicy()
        assertEquals("customer-1", afterBack.customerId)
        assertNull(afterBack.selectedPolicyId)

        val secondScope = afterBack.selectCustomer("customer-2")
        assertEquals(listOf("policy-2"), policiesInScope(policies, secondScope).map { it.id })
        assertNull(selectedPolicyInScope(policies, secondScope))
        assertEquals(mapOf("customer-1" to 1, "customer-2" to 1), policyCountsByCustomerId(policies))
    }

    @Test
    fun explicitAllPoliciesIsSeparateFromDefaultCustomerSelection() {
        val policies = listOf(
            PolicyEntity(id = "policy-1", customerId = "customer-1"),
            PolicyEntity(id = "policy-2", customerId = "customer-2"),
        )

        assertTrue(policiesInScope(policies, PolicyBrowseState()).isEmpty())
        assertEquals(2, policiesInScope(policies, PolicyBrowseState().showAll()).size)
    }

    @Test
    fun policiesInScopeSortsMonthlyPremiumDescendingAndKeepsMissingLast() {
        val policies = listOf(
            PolicyEntity(id = "missing", customerId = "customer-1", premium = null),
            PolicyEntity(id = "small", customerId = "customer-1", premium = 10000),
            PolicyEntity(id = "large", customerId = "customer-1", premium = 50000),
        )

        assertEquals(
            listOf("large", "small", "missing"),
            policiesInScope(policies, PolicyBrowseState().selectCustomer("customer-1")).map { it.id },
        )
    }

    @Test
    fun policyDisplayFormattingUsesKoreanCodesAndDoesNotBuildMissingRange() {
        assertEquals("매월", paymentCycleLabel("MONTHLY"))
        assertEquals("기타 (FUTURE_CYCLE)", paymentCycleLabel("FUTURE_CYCLE"))
        assertEquals("미제공", policyPeriodLabel(null, null))
        assertEquals("2026년 1월 1일 ~ 종료일 미제공", policyPeriodLabel("2026-01-01", null))
    }
}
