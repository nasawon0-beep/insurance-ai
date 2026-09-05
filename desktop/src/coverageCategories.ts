export const COVERAGE_CATEGORIES: { title: string; items: string[] }[] = [
  { title: "사망 · 후유장해", items: ["질병사망", "상해사망", "질병 3%+ 후유장해", "상해 3%+ 후유장해"] },
  { title: "암", items: ["일반암진단비", "유사암진단비", "통합암진단비", "특정암진단비"] },
  { title: "뇌혈관질환", items: ["뇌혈관진단비", "뇌졸중진단비", "뇌출혈진단비", "뇌 산정특례진단비"] },
  { title: "심장질환", items: ["허혈성심장질환진단비", "급성심근경색증진단비", "심장 산정특례진단비"] },
  { title: "치매", items: ["중증치매진단", "경증치매진단"] },
  { title: "실손의료비", items: ["질병입원", "질병통원", "상해입원", "상해통원"] },
  { title: "수술비", items: ["암수술비", "뇌혈관수술비", "허혈심장질환수술비", "질병수술비", "상해수술비", "질병종수술", "상해종수술"] },
  { title: "입원비(일당) · 치료비", items: [
    "질병일당", "상해일당", "질병 간병인지원 일당", "상해 간병인지원 일당",
    "질병 간병인사용 일당", "상해 간병인사용 일당", "1인실 입원일당",
    "고액항암치료비(표적)", "중입자방사선치료비", "암주요치료비", "2대질환주요치료비",
  ]},
  { title: "운전자 · 법률배상 · 기타", items: [
    "교통사고처리지원금", "변호사선임비용", "벌금", "자동차사고부상치료비",
    "일상생활배상책임", "민사소송법률비용", "화재벌금",
    "치아보철치료비", "치아보존치료비", "화상진단비", "골절진단비",
  ]},
];
export const FALLBACK_CATEGORY = "기타";

// PDF 파서(_parse_coverage_status)가 뽑는 항목명은 원문 PDF의 띄어쓰기를 그대로 따라가서
// "일반암진단비"/"일반암 진단비"처럼 공백 유무가 문서마다 갈린다 — 내부 공백을 지우고 비교.
const stripSpaces = (s: string) => s.replace(/\s+/g, "");

/** 항목명 → 카테고리 제목. 표준 50개 체크리스트에 없는 이름은 FALLBACK_CATEGORY. */
export function categoryOf(name: string): string {
  const n = stripSpaces((name ?? "").trim());
  for (const cat of COVERAGE_CATEGORIES) {
    if (cat.items.some((item) => stripSpaces(item) === n)) return cat.title;
  }
  return FALLBACK_CATEGORY;
}

/** rows 를 고정 카테고리 순서로 그룹핑. 항목 없는 카테고리는 결과에서 빠짐. "기타"는 항상 마지막. */
export function groupByCategory<T extends { name: string }>(rows: T[]): { title: string; rows: T[] }[] {
  const buckets = new Map<string, T[]>();
  for (const row of rows) {
    const cat = categoryOf(row.name);
    if (!buckets.has(cat)) buckets.set(cat, []);
    buckets.get(cat)!.push(row);
  }
  const ordered: { title: string; rows: T[] }[] = [];
  for (const cat of COVERAGE_CATEGORIES) {
    if (buckets.has(cat.title)) { ordered.push({ title: cat.title, rows: buckets.get(cat.title)! }); buckets.delete(cat.title); }
  }
  if (buckets.has(FALLBACK_CATEGORY)) ordered.push({ title: FALLBACK_CATEGORY, rows: buckets.get(FALLBACK_CATEGORY)! });
  return ordered;
}
