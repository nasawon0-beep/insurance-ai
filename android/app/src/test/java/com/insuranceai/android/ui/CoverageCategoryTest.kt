package com.insuranceai.android.ui

import org.junit.Assert.*
import org.junit.Test

class CoverageCategoryTest {
    @Test fun filteringKeepsOriginalRowsAndIndicesIncludingZeroNullUnknown() {
        val rows = parseCoverageAnalysis("""{"coverage_items":[
            {"coverage_name":"암수술비","coverage_group":"수술비","current_amount":0,"recommended_amount":null,"pct":0,"shortage_amount":-10000,"raw_text":"합성 근거"},
            {"coverage_name":"가상 항목","coverage_group":"새분류"},
            {"coverage_name":"진단","coverage_group":"암"},
            {"coverage_name":"분류없음","coverage_group":null}
        ]}""").rows
        val filtered = filterCoverageRows(rows, "수술비")
        assertEquals(listOf(0), filtered.map { it.index })
        assertSame(rows[0], filtered.single().value)
        assertEquals("0원", filtered.single().value.current.display)
        assertEquals("값 없음", filtered.single().value.recommended.display)
        assertEquals(listOf(1, 3), filterCoverageRows(rows, "기타").map { it.index })
        assertEquals(rows, filterCoverageRows(rows, "전체").map { it.value })
        assertTrue(filterCoverageRows(rows, "치매").isEmpty())
        assertTrue(filterCoverageRows(emptyList(), "전체").isEmpty())
    }

    @Test fun pcCategoryOrderAndUnknownArePreserved() {
        assertEquals(listOf("전체", "사망·후유장해", "암", "뇌혈관질환", "심장질환", "치매", "실손의료비", "수술비", "입원비(일당)·치료비", "운전자·법률배상·기타", "기타"), coverageCategoryTabs)
        assertEquals("사망·후유장해", coverageCategory("후유장해"))
        assertEquals("입원비(일당)·치료비", coverageCategory("입원비 / 일당"))
        assertEquals("운전자·법률배상·기타", coverageCategory("치아 / 화상 / 골절"))
        listOf(null, "", "알수없는 분류").forEach { assertEquals("기타", coverageCategory(it)) }
    }
}
