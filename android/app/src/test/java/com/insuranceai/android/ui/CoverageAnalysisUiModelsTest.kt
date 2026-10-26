package com.insuranceai.android.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CoverageAnalysisUiModelsTest {
    @Test
    fun parsesCurrentSchemaWithoutInventingTotals() {
        val parsed = parseCoverageAnalysis("""
            {"coverage_items":[
              {"coverage_name":"일반암 진단비","coverage_group":"진단비","current_amount":20000000,"recommended_amount":50000000,"status":"부족","confidence":0.92},
              {"coverage_name":"뇌혈관 진단비","current_amount":null,"status":"확인필요"},
              {"coverage_name":"상해후유장해","current_amount":0,"recommended_amount":"3,000만원","extra_key":"원문보존"}
            ],"overall":"합성 요약"}
        """.trimIndent())

        assertEquals(3, parsed.rows.size)
        assertEquals("2,000만원", parsed.rows[0].current.display)
        assertEquals("5,000만원", parsed.rows[0].recommended.display)
        assertEquals("값 없음", parsed.rows[1].current.display)
        assertEquals("미제공", parsed.rows[1].recommended.display)
        assertEquals("0원", parsed.rows[2].current.display)
        assertEquals("3,000만원", parsed.rows[2].recommended.display)
        assertEquals(1, parsed.rows[2].additionalFields.size)
        assertFalse(parsed.summary.contains("합계"))
    }

    @Test
    fun preservesInvalidAndUnknownInputForDetails() {
        val invalid = parseCoverageAnalysis("not-json")
        assertTrue(invalid.parseFailed)
        assertEquals("not-json", invalid.rawSource)
        assertTrue(invalid.rows.isEmpty())

        val unknown = parseCoverageAnalysis("{\"unexpected\":{\"nested\":true}}")
        assertFalse(unknown.parseFailed)
        assertTrue(unknown.unsupportedSchema)
        assertTrue(unknown.rawSource.contains("unexpected"))
    }

    @Test
    fun businessCodesAndUnknownValuesRemainUnderstandable() {
        assertEquals("전체 보장분석", businessLabel("coverage_analysis_full"))
        assertEquals("정상 유지", policyStatusLabel("ACTIVE"))
        assertEquals("기타 (future_code)", businessLabel("future_code"))
        assertEquals("값 없음", businessLabel(null))
    }

    @Test
    fun knownProducerFieldsUseKoreanLabelsUnitsAndConfirmedNegativeGapMeaning() {
        val parsed = parseCoverageAnalysis("""
            {"coverage_items":[
              {"coverage_name":"부족 보장","pct":40,"shortage_amount":30000000,"source_page":7},
              {"coverage_name":"초과 보장","pct":100,"shortage_amount":-170600000,"source_page":8},
              {"coverage_name":"같은 금액","pct":0,"shortage_amount":0,"source_page":null}
            ]}
        """.trimIndent())

        assertEquals("충족률", parsed.rows[0].fulfillment?.label)
        assertEquals("40%", parsed.rows[0].fulfillment?.display)
        assertEquals("부족금액", parsed.rows[0].difference?.label)
        assertEquals("3,000만원", parsed.rows[0].difference?.display)
        assertEquals("자료 쪽", parsed.rows[0].sourcePage?.label)
        assertEquals("7쪽", parsed.rows[0].sourcePage?.display)

        assertEquals("초과금액", parsed.rows[1].difference?.label)
        assertEquals("1억 7,060만원", parsed.rows[1].difference?.display)
        assertEquals("부족·초과금액", parsed.rows[2].difference?.label)
        assertEquals("0원", parsed.rows[2].difference?.display)
        assertEquals("값 없음", parsed.rows[2].sourcePage?.display)
    }

    @Test
    fun knownNullZeroMissingUnknownAndNegativeValuesArePreservedWithoutRecalculation() {
        val parsed = parseCoverageAnalysis("""
            {"coverage_items":[
              {"coverage_name":"누락"},
              {"coverage_name":"널","pct":null,"shortage_amount":null},
              {"coverage_name":"미지","pct":"계산불가","shortage_amount":"원문금액","source_page":-2,"future_key":{"raw":true}}
            ]}
        """.trimIndent())

        assertNull(parsed.rows[0].fulfillment)
        assertNull(parsed.rows[0].difference)
        assertEquals("값 없음", parsed.rows[1].fulfillment?.display)
        assertEquals("값 없음", parsed.rows[1].difference?.display)
        assertEquals("계산불가 · 원본값 확인 필요", parsed.rows[2].fulfillment?.display)
        assertEquals("원문금액 · 형식 확인 필요", parsed.rows[2].difference?.display)
        assertEquals("-2쪽 · 원본값 확인 필요", parsed.rows[2].sourcePage?.display)
        assertTrue(parsed.rows[2].additionalFields.containsKey("future_key"))
    }
}
