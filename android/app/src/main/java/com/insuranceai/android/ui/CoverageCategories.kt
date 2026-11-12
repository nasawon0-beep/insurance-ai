package com.insuranceai.android.ui

// Mirrors desktop/src/CoverageSourceSnapshot.tsx's displayed tabs and mapping.
// Only presentation grouping: never infer a category from a coverage name or recalculate values.
val coverageCategoryTabs = listOf(
    "전체", "사망·후유장해", "암", "뇌혈관질환", "심장질환", "치매", "실손의료비",
    "수술비", "입원비(일당)·치료비", "운전자·법률배상·기타", "기타",
)
private val coverageCategoryMapping = listOf(
    "뇌혈관질환" to "뇌혈관질환", "심장질환" to "심장질환", "실손의료비" to "실손의료비",
    "후유장해" to "사망·후유장해", "배상책임" to "운전자·법률배상·기타",
    "사망" to "사망·후유장해", "암" to "암", "뇌" to "뇌혈관질환", "심장" to "심장질환",
    "치매" to "치매", "실손" to "실손의료비", "수술" to "수술비",
    "입원비" to "입원비(일당)·치료비", "일당" to "입원비(일당)·치료비", "치료비" to "입원비(일당)·치료비",
    "운전자" to "운전자·법률배상·기타", "법률" to "운전자·법률배상·기타",
    "배상" to "운전자·법률배상·기타", "치아" to "운전자·법률배상·기타",
    "화상" to "운전자·법률배상·기타", "골절" to "운전자·법률배상·기타",
)
fun filterCoverageRows(rows: List<UiCoverageRow>, selected: String): List<IndexedValue<UiCoverageRow>> =
    rows.withIndex().filter { selected == "전체" || coverageCategory(it.value.group) == selected }

fun coverageCategory(group: String?): String =
    coverageCategoryMapping.firstOrNull { group.orEmpty().contains(it.first) }?.second ?: "기타"
