import type { CoverageRow } from "./coverageRows";

export type CoverageCatalogItem = {
  id: string;
  name: string;
  category: string;
  recommended: number;
};

export const COVERAGE_CATALOG: CoverageCatalogItem[] = [
  { id: "death:01", name: "질병사망", category: "사망", recommended: 100000000 },
  { id: "death:02", name: "상해사망", category: "사망", recommended: 100000000 },
  { id: "disability:03", name: "질병 3% 이상 후유장해", category: "후유장해", recommended: 50000000 },
  { id: "disability:04", name: "상해 3% 이상 후유장해", category: "후유장해", recommended: 100000000 },
  { id: "cancer:05", name: "일반암 진단비", category: "암", recommended: 50000000 },
  { id: "cancer:06", name: "유사암 진단비", category: "암", recommended: 10000000 },
  { id: "cerebrovascular:07", name: "뇌혈관 진단비", category: "뇌혈관질환", recommended: 50000000 },
  { id: "cerebrovascular:08", name: "뇌졸중 진단비", category: "뇌혈관질환", recommended: 30000000 },
  { id: "cerebrovascular:09", name: "뇌출혈 진단비", category: "뇌혈관질환", recommended: 30000000 },
  { id: "heart:10", name: "허혈성심장질환 진단비", category: "심장질환", recommended: 50000000 },
  { id: "heart:11", name: "급성심근경색증 진단비", category: "심장질환", recommended: 30000000 },
  { id: "dementia:12", name: "중증치매진단", category: "치매", recommended: 30000000 },
  { id: "dementia:13", name: "경증치매진단", category: "치매", recommended: 10000000 },
  { id: "indemnity_medical:14", name: "질병입원", category: "실손의료비", recommended: 50000000 },
  { id: "indemnity_medical:15", name: "질병통원", category: "실손의료비", recommended: 250000 },
  { id: "indemnity_medical:16", name: "상해입원", category: "실손의료비", recommended: 50000000 },
  { id: "indemnity_medical:17", name: "상해통원", category: "실손의료비", recommended: 250000 },
  { id: "surgery:18", name: "암수술비", category: "수술비", recommended: 10000000 },
  { id: "surgery:19", name: "뇌혈관수술비", category: "수술비", recommended: 10000000 },
  { id: "surgery:20", name: "허혈심장질환수술비", category: "수술비", recommended: 10000000 },
  { id: "surgery:21", name: "질병수술비", category: "수술비", recommended: 500000 },
  { id: "surgery:22", name: "상해수술비", category: "수술비", recommended: 500000 },
  { id: "surgery:23", name: "질병종수술", category: "수술비", recommended: 5000000 },
  { id: "surgery:24", name: "상해종수술", category: "수술비", recommended: 5000000 },
  { id: "hospitalization:25", name: "질병일당", category: "입원비 / 일당", recommended: 30000 },
  { id: "hospitalization:26", name: "상해일당", category: "입원비 / 일당", recommended: 30000 },
  { id: "hospitalization:27", name: "질병 간병인지원 입원일당", category: "입원비 / 일당", recommended: 150000 },
  { id: "hospitalization:28", name: "상해 간병인지원 입원일당", category: "입원비 / 일당", recommended: 150000 },
  { id: "hospitalization:29", name: "질병 간병인사용 입원일당", category: "입원비 / 일당", recommended: 150000 },
  { id: "hospitalization:30", name: "상해 간병인사용 입원일당", category: "입원비 / 일당", recommended: 150000 },
  { id: "hospitalization:31", name: "1인실 입원일당", category: "입원비 / 일당", recommended: 100000 },
  { id: "treatment:32", name: "고액항암 치료비(표적)", category: "치료비", recommended: 50000000 },
  { id: "treatment:33", name: "중입자방사선치료비", category: "치료비", recommended: 50000000 },
  { id: "treatment:34", name: "암 주요치료비", category: "치료비", recommended: 100000000 },
  { id: "treatment:35", name: "2대질환 주요치료비", category: "치료비", recommended: 30000000 },
  { id: "driver:36", name: "교통사고처리 지원금", category: "운전자", recommended: 200000000 },
  { id: "driver:37", name: "변호사 선임비용", category: "운전자", recommended: 50000000 },
  { id: "driver:38", name: "벌금", category: "운전자", recommended: 30000000 },
  { id: "driver:39", name: "자동차사고 부상치료비", category: "운전자", recommended: 10000000 },
  { id: "liability_legal:40", name: "일상생활 배상책임", category: "법률 / 배상책임", recommended: 100000000 },
  { id: "liability_legal:41", name: "민사소송 법률비용", category: "법률 / 배상책임", recommended: 20000000 },
  { id: "liability_legal:42", name: "화재벌금", category: "법률 / 배상책임", recommended: 20000000 },
  { id: "dental_burn_fracture:43", name: "치아보철 치료비", category: "치아 / 화상 / 골절", recommended: 1000000 },
  { id: "dental_burn_fracture:44", name: "치아보존 치료비", category: "치아 / 화상 / 골절", recommended: 300000 },
  { id: "dental_burn_fracture:45", name: "화상 진단비", category: "치아 / 화상 / 골절", recommended: 300000 },
  { id: "dental_burn_fracture:46", name: "골절 진단비", category: "치아 / 화상 / 골절", recommended: 300000 },
];

export function normalizeCoverageCatalogName(name: string): string {
  return name.replace(/\s+/g, "").trim();
}

export function findCoverageCatalogItem(name: string): CoverageCatalogItem | undefined {
  const normalized = normalizeCoverageCatalogName(name);
  return COVERAGE_CATALOG.find((item) => normalizeCoverageCatalogName(item.name) === normalized);
}

export function expandCoverageRowsWithCatalog(rows: CoverageRow[]): CoverageRow[] {
  const rowByName = new Map(rows.map((row) => [normalizeCoverageCatalogName(row.name), row]));
  return COVERAGE_CATALOG.map((item) => {
    const matched = rowByName.get(normalizeCoverageCatalogName(item.name));
    return matched ?? {
      name: item.name,
      status: "미가입",
      pct: 0,
      current: "0만원",
      recommended: `${Math.round(item.recommended / 10000).toLocaleString("ko-KR")}만원`,
    };
  });
}
