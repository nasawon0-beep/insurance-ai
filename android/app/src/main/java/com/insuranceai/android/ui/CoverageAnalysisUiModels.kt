package com.insuranceai.android.ui

import org.json.JSONArray
import org.json.JSONObject
import java.text.NumberFormat
import java.util.Locale

private object MissingCoverageValue

data class CoverageAmount(val display: String, val needsReview: Boolean = false)
data class CoverageDetailField(val label: String, val display: String, val needsReview: Boolean = false)
data class UiCoverageRow(
    val name: String,
    val group: String?,
    val current: CoverageAmount,
    val recommended: CoverageAmount,
    val status: String?,
    val confidence: String?,
    val evidence: String?,
    val fulfillment: CoverageDetailField?,
    val difference: CoverageDetailField?,
    val sourcePage: CoverageDetailField?,
    val additionalFields: Map<String, String>,
)
data class UiCoverageAnalysis(
    val rows: List<UiCoverageRow>,
    val summary: String,
    val parseFailed: Boolean,
    val unsupportedSchema: Boolean,
    val rawSource: String,
)

private val knownCoverageFields = setOf(
    "coverage_name", "name", "coverage_group", "current_amount", "current",
    "recommended_amount", "recommended", "status", "confidence", "raw_text",
    "pct", "shortage_amount", "source_page"
)

fun coverageAmountMissing() = CoverageAmount("미제공")
fun coverageAmountNull() = CoverageAmount("값 없음")

fun coverageAmountValue(value: Any?): CoverageAmount {
    if (value == null || value === JSONObject.NULL) return coverageAmountNull()
    if (value is Number) {
        val won = value.toLong()
        if (won == 0L) return CoverageAmount("0원")
        if (won % 10_000L == 0L) return CoverageAmount("${NumberFormat.getIntegerInstance(Locale.KOREA).format(won / 10_000L)}만원")
        return CoverageAmount("${NumberFormat.getIntegerInstance(Locale.KOREA).format(won)}원")
    }
    val text = value.toString().trim()
    if (text.isEmpty()) return CoverageAmount("입력 없음")
    if (text.matches(Regex("[0-9,]+(?:\\.[0-9]+)?\\s*(?:원|만원|천만원|억원)"))) return CoverageAmount(text)
    return CoverageAmount("$text · 형식 확인 필요", needsReview = true)
}

private fun amountFrom(item: JSONObject, primary: String, legacy: String): CoverageAmount {
    val value: Any = when {
        item.has(primary) -> if (item.isNull(primary)) JSONObject.NULL else item.get(primary)
        item.has(legacy) -> if (item.isNull(legacy)) JSONObject.NULL else item.get(legacy)
        else -> MissingCoverageValue
    }
    return if (value === MissingCoverageValue) coverageAmountMissing() else coverageAmountValue(value)
}

private fun formattedAbsoluteWon(value: Long): String {
    val absolute = kotlin.math.abs(value)
    if (absolute == 0L) return "0원"
    val eok = absolute / 100_000_000L
    val man = (absolute % 100_000_000L) / 10_000L
    val won = absolute % 10_000L
    return buildList {
        if (eok > 0) add("${NumberFormat.getIntegerInstance(Locale.KOREA).format(eok)}억")
        if (man > 0) add("${NumberFormat.getIntegerInstance(Locale.KOREA).format(man)}만원")
        if (won > 0 || isEmpty()) add("${NumberFormat.getIntegerInstance(Locale.KOREA).format(won)}원")
    }.joinToString(" ")
}

private fun fulfillmentFrom(item: JSONObject): CoverageDetailField? {
    if (!item.has("pct")) return null
    if (item.isNull("pct")) return CoverageDetailField("충족률", "값 없음")
    val value = item.get("pct")
    if (value is Number) {
        val numeric = value.toDouble()
        val display = if (numeric % 1.0 == 0.0) "${numeric.toLong()}%" else "$numeric%"
        return CoverageDetailField("충족률", display, numeric !in 0.0..100.0)
    }
    return CoverageDetailField("충족률", "${value.toString().trim()} · 원본값 확인 필요", true)
}

private fun differenceFrom(item: JSONObject): CoverageDetailField? {
    if (!item.has("shortage_amount")) return null
    if (item.isNull("shortage_amount")) return CoverageDetailField("부족·초과금액", "값 없음")
    val value = item.get("shortage_amount")
    if (value is Number) {
        val numeric = value.toLong()
        return when {
            numeric > 0 -> CoverageDetailField("부족금액", formattedAbsoluteWon(numeric))
            numeric < 0 -> CoverageDetailField("초과금액", formattedAbsoluteWon(numeric))
            else -> CoverageDetailField("부족·초과금액", "0원")
        }
    }
    return CoverageDetailField("부족·초과금액", "${value.toString().trim()} · 형식 확인 필요", true)
}

private fun sourcePageFrom(item: JSONObject): CoverageDetailField? {
    if (!item.has("source_page")) return null
    if (item.isNull("source_page")) return CoverageDetailField("자료 쪽", "값 없음")
    val value = item.get("source_page")
    if (value is Number) {
        val page = value.toLong()
        return CoverageDetailField("자료 쪽", if (page >= 0) "${page}쪽" else "${page}쪽 · 원본값 확인 필요", page < 0)
    }
    return CoverageDetailField("자료 쪽", "${value.toString().trim()} · 원본값 확인 필요", true)
}

fun parseCoverageAnalysis(raw: String): UiCoverageAnalysis {
    val source = raw.trim()
    val root = runCatching { JSONObject(source) }.getOrNull()
    val arrayRoot = if (root == null) runCatching { JSONArray(source) }.getOrNull() else null
    if (root == null && arrayRoot == null) {
        return UiCoverageAnalysis(emptyList(), "분석 형식을 읽을 수 없음 · PC에서 확인", true, false, raw)
    }
    val items = when {
        arrayRoot != null -> arrayRoot
        root?.optJSONArray("coverage_items") != null -> root.getJSONArray("coverage_items")
        else -> null
    }
    if (items == null) {
        return UiCoverageAnalysis(emptyList(), "지원하지 않는 분석 형식 · 원본 데이터는 보존됨", false, true, raw)
    }
    val rows = buildList {
        for (index in 0 until items.length()) {
            val item = items.optJSONObject(index)
            if (item == null) {
                add(UiCoverageRow("항목 ${index + 1} · 형식 확인 필요", null, coverageAmountMissing(), coverageAmountMissing(), null, null, items.opt(index)?.toString(), null, null, null, emptyMap()))
                continue
            }
            val name = item.optString("coverage_name").ifBlank { item.optString("name") }.ifBlank { "보장명 미제공" }
            val additional = linkedMapOf<String, String>()
            item.keys().forEach { key -> if (key !in knownCoverageFields) additional[key] = if (item.isNull(key)) "값 없음" else item.get(key).toString() }
            add(UiCoverageRow(
                name = name,
                group = item.optString("coverage_group").ifBlank { null },
                current = amountFrom(item, "current_amount", "current"),
                recommended = amountFrom(item, "recommended_amount", "recommended"),
                status = item.optString("status").ifBlank { null },
                confidence = if (item.has("confidence") && !item.isNull("confidence")) item.get("confidence").toString() else null,
                evidence = item.optString("raw_text").ifBlank { null },
                fulfillment = fulfillmentFrom(item),
                difference = differenceFrom(item),
                sourcePage = sourcePageFrom(item),
                additionalFields = additional,
            ))
        }
    }
    val summary = root?.opt("overall")?.takeUnless { it === JSONObject.NULL }?.toString()
        ?: "보장 ${rows.size}항목"
    return UiCoverageAnalysis(rows, summary, false, false, raw)
}

fun businessLabel(value: String?): String {
    val clean = value?.trim().orEmpty()
    if (clean.isEmpty()) return "값 없음"
    return when (clean.lowercase()) {
        "coverage_analysis_full" -> "전체 보장분석"
        "coverage_analysis_short" -> "간편 보장분석"
        "coverage_analysis_detail_only" -> "보장 상세표"
        "coverage_analysis" -> "보장분석"
        "phone", "telephone" -> "전화 상담"
        "visit", "in_person" -> "대면 상담"
        "online" -> "온라인 상담"
        else -> if (clean.matches(Regex("[a-z0-9_]+", RegexOption.IGNORE_CASE))) "기타 ($clean)" else clean
    }
}

fun policyStatusLabel(value: String?): String = when (value?.trim()?.uppercase()) {
    "ACTIVE" -> "정상 유지"
    "PROPOSED" -> "가입 제안"
    "REFERENCE" -> "참고 자료"
    "PENDING_REVIEW" -> "검토 필요"
    "CANCELLED", "TERMINATED" -> "해지"
    "EXPIRED" -> "만료"
    null, "" -> "상태 미제공"
    else -> "기타 (${value.trim()})"
}
