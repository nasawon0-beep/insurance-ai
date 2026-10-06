import type { CoverageRow } from "./coverageRows";

export type CoverageCatalogItem = {
  id: string;
  name: string;
  category: string;
  recommended: number;
  aliases?: string[];
};

export const COVERAGE_CATALOG: CoverageCatalogItem[] = [
  { id: "death:01", name: "질병사망", category: "사망", recommended: 100000000 },
  { id: "death:02", name: "상해사망", category: "사망", recommended: 100000000 },
  { id: "disability:03", name: "질병 3%이상 후유장해", category: "후유장해", recommended: 50000000, aliases: ["질병 3% 이상 후유장해"] },
  { id: "disability:04", name: "상해 3%이상 후유장해", category: "후유장해", recommended: 100000000, aliases: ["상해 3% 이상 후유장해"] },
  { id: "cancer:05", name: "일반암 진단비", category: "암", recommended: 50000000 },
  { id: "cancer:06", name: "유사암 진단비", category: "암", recommended: 10000000 },
  { id: "cancer:07", name: "통합암 진단비", category: "암", recommended: 50000000, aliases: ["통합암진단비", "통합암진단비(유사암제외)"] },
  { id: "cancer:08", name: "특정암 진단비", category: "암", recommended: 50000000, aliases: ["특정암진단비", "전이암진단비", "림프절전이암진단비", "특정전이암진단비", "10대특정암진단비", "15대특정암진단비", "4대고액암진단비", "특정소액암진단비", "특정소화기암진단비"] },
  { id: "cerebrovascular:09", name: "뇌혈관 진단비", category: "뇌혈관질환", recommended: 50000000 },
  { id: "cerebrovascular:10", name: "뇌졸중 진단비", category: "뇌혈관질환", recommended: 30000000 },
  { id: "cerebrovascular:11", name: "뇌출혈 진단비", category: "뇌혈관질환", recommended: 30000000 },
  { id: "cerebrovascular:12", name: "뇌산정특례대상 진단비", category: "뇌혈관질환", recommended: 10000000, aliases: ["뇌산정특례", "뇌혈관 산정특례"] },
  { id: "heart:13", name: "허혈성심장질환 진단비", category: "심장질환", recommended: 50000000 },
  { id: "heart:14", name: "급성심근경색증 진단비", category: "심장질환", recommended: 30000000, aliases: ["급성심근경색 진단비"] },
  { id: "heart:15", name: "심장산정특례대상 진단비", category: "심장질환", recommended: 10000000, aliases: ["심장산정특례", "심장질환 산정특례"] },
  { id: "dementia:16", name: "중증치매진단", category: "치매", recommended: 30000000 },
  { id: "dementia:17", name: "경증치매진단", category: "치매", recommended: 10000000 },
  { id: "indemnity_medical:18", name: "질병입원", category: "실손의료비", recommended: 50000000 },
  { id: "indemnity_medical:19", name: "질병통원", category: "실손의료비", recommended: 250000 },
  { id: "indemnity_medical:20", name: "상해입원", category: "실손의료비", recommended: 50000000 },
  { id: "indemnity_medical:21", name: "상해통원", category: "실손의료비", recommended: 250000 },
  { id: "surgery:22", name: "암수술비", category: "수술비", recommended: 10000000 },
  { id: "surgery:23", name: "뇌혈관수술비", category: "수술비", recommended: 10000000 },
  { id: "surgery:24", name: "허혈심장질환수술비", category: "수술비", recommended: 10000000 },
  { id: "surgery:25", name: "질병수술비", category: "수술비", recommended: 500000 },
  { id: "surgery:26", name: "상해수술비", category: "수술비", recommended: 500000 },
  { id: "surgery:27", name: "질병종수술", category: "수술비", recommended: 5000000, aliases: ["질병 종수술", "질병 1~5종 수술비", "질병 1-5종 수술비", "질병 1~5종 수술비(5종)", "질병 1~7종 수술비", "[건강]질병 1~5종 수술비(5종)"] },
  { id: "surgery:28", name: "상해종수술", category: "수술비", recommended: 5000000, aliases: ["상해 종수술", "상해 1~5종 수술비", "상해 1-5종 수술비"] },
  { id: "hospitalization:29", name: "질병일당", category: "입원비 / 일당", recommended: 30000 },
  { id: "hospitalization:30", name: "상해일당", category: "입원비 / 일당", recommended: 30000 },
  { id: "hospitalization:31", name: "질병 간병인지원 입원일당", category: "입원비 / 일당", recommended: 150000 },
  { id: "hospitalization:32", name: "상해 간병인지원 입원일당", category: "입원비 / 일당", recommended: 150000 },
  { id: "hospitalization:33", name: "질병 간병인사용 입원일당", category: "입원비 / 일당", recommended: 150000 },
  { id: "hospitalization:34", name: "상해 간병인사용 입원일당", category: "입원비 / 일당", recommended: 150000 },
  { id: "hospitalization:35", name: "1인실 입원일당", category: "입원비 / 일당", recommended: 100000 },
  { id: "treatment:36", name: "고액항암 치료비(표적)", category: "치료비", recommended: 50000000 },
  { id: "treatment:37", name: "중입자방사선치료비", category: "치료비", recommended: 50000000 },
  { id: "treatment:38", name: "암 주요치료비", category: "치료비", recommended: 100000000 },
  { id: "treatment:39", name: "2대질환 주요치료비", category: "치료비", recommended: 30000000 },
  { id: "driver:40", name: "교통사고처리 지원금", category: "운전자", recommended: 200000000 },
  { id: "driver:41", name: "변호사 선임비용", category: "운전자", recommended: 50000000 },
  { id: "driver:42", name: "벌금", category: "운전자", recommended: 30000000 },
  { id: "driver:43", name: "자동차사고 부상치료비", category: "운전자", recommended: 10000000 },
  { id: "liability_legal:44", name: "일상생활 배상책임", category: "법률 / 배상책임", recommended: 100000000 },
  { id: "liability_legal:45", name: "민사소송 법률비용", category: "법률 / 배상책임", recommended: 20000000 },
  { id: "liability_legal:46", name: "화재벌금", category: "법률 / 배상책임", recommended: 20000000 },
  { id: "dental_burn_fracture:47", name: "치아보철 치료비", category: "치아 / 화상 / 골절", recommended: 1000000 },
  { id: "dental_burn_fracture:48", name: "치아보존 치료비", category: "치아 / 화상 / 골절", recommended: 300000 },
  { id: "dental_burn_fracture:49", name: "화상 진단비", category: "치아 / 화상 / 골절", recommended: 300000 },
  { id: "dental_burn_fracture:50", name: "골절 진단비", category: "치아 / 화상 / 골절", recommended: 300000 },
];

export function normalizeCoverageCatalogName(name: string): string {
  return name.replace(/\s+/g, "").replace(/3%이상/g, "3%이상").trim();
}

function catalogNames(item: CoverageCatalogItem): string[] {
  return [item.name, ...(item.aliases ?? [])];
}

export function findCoverageCatalogItem(name: string): CoverageCatalogItem | undefined {
  const normalized = normalizeCoverageCatalogName(name);
  return COVERAGE_CATALOG.find((item) => catalogNames(item).some((candidate) => normalizeCoverageCatalogName(candidate) === normalized));
}

export function expandCoverageRowsWithCatalog(rows: CoverageRow[]): CoverageRow[] {
  const rowByName = new Map(rows.map((row) => [normalizeCoverageCatalogName(row.name), row]));
  return COVERAGE_CATALOG.map((item) => {
    const matched = catalogNames(item).map((candidate) => rowByName.get(normalizeCoverageCatalogName(candidate))).find(Boolean);
    return matched ?? {
      name: item.name,
      status: "미가입",
      pct: 0,
      current: "0만원",
      recommended: `${Math.round(item.recommended / 10000).toLocaleString("ko-KR")}만원`,
    };
  });
}
