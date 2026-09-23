import { useEffect, useState } from "react";
import { engineFetch } from "./engine";
import { LineChart, Line, BarChart, Bar, PieChart, Pie, Cell, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from "recharts";

type StatsSummary = {
  period: string;
  customers: {
    total: number;
    new_this_period: number;
    with_policies: number;
    avg_policies_per_customer: number;
  };
  policies: {
    total: number;
    active: number;
    total_monthly_premium: number;
    avg_premium: number;
    expiring_30days: number;
  };
  consultations: {
    total: number;
    this_period: number;
    pending_followups: number;
    channels: Record<string, number>;
  };
  regional_top5: Array<{ region: string; customers: number; total_premium: number }>;
  insurer_top5: Array<{ insurer: string; policies: number; total_premium: number }>;
};

type ChartData = {
  chart_type: string;
  data: {
    labels: string[];
    datasets: Array<{
      label: string;
      data: number[];
    }>;
  };
};

const COLORS = ["#2563eb", "#16a34a", "#dc2626", "#ca8a04", "#9333ea", "#0891b2", "#ea580c"];

export default function EnhancedDashboard() {
  const [summary, setSummary] = useState<StatsSummary | null>(null);
  const [monthlyTrend, setMonthlyTrend] = useState<ChartData | null>(null);
  const [period, setPeriod] = useState<"month" | "quarter" | "year">("month");
  const [year, setYear] = useState(new Date().getFullYear());
  const [month, setMonth] = useState(new Date().getMonth() + 1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadData();
  }, [period, year, month]);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    
    try {
      // 통계 요약 조회
      const summaryParams = new URLSearchParams({
        period,
        year: String(year),
        ...(period === "month" ? { month: String(month) } : {}),
      });
      
      const summaryRes = await engineFetch(`/statistics/summary?${summaryParams}`);
      if (!summaryRes.ok) throw new Error(`HTTP ${summaryRes.status}`);
      setSummary(await summaryRes.json());
      
      // 월별 추이 차트
      const chartRes = await engineFetch(`/statistics/charts?chart_type=monthly_trend&year=${year}`);
      if (!chartRes.ok) throw new Error(`HTTP ${chartRes.status}`);
      setMonthlyTrend(await chartRes.json());
      
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const exportExcel = async () => {
    try {
      const r = await engineFetch(`/export/customers?format=xlsx`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `고객목록_${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  if (loading) {
    return (
      <div style={{ maxWidth: 1200, margin: "24px auto", padding: "0 16px" }}>
        <p style={{ color: "#888" }}>통계를 불러오는 중...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ maxWidth: 1200, margin: "24px auto", padding: "0 16px" }}>
        <p style={{ color: "#b00" }}>오류: {error}</p>
      </div>
    );
  }

  if (!summary) return null;

  // 월별 추이 차트 데이터 변환
  const trendData = monthlyTrend?.data.labels.map((label, i) => ({
    month: label,
    신규고객: monthlyTrend.data.datasets[0]?.data[i] || 0,
    신규계약: monthlyTrend.data.datasets[1]?.data[i] || 0,
  })) || [];

  // 지역별 파이 차트 데이터
  const regionalData = summary.regional_top5.map((item) => ({
    name: item.region,
    value: item.customers,
  }));

  // 보험사별 바 차트 데이터
  const insurerData = summary.insurer_top5.map((item) => ({
    name: item.insurer,
    계약수: item.policies,
    월보험료: Math.round(item.total_premium / 10000), // 만원 단위
  }));

  const formatCurrency = (value: number) => {
    if (value >= 100000000) return `${(value / 100000000).toFixed(1)}억`;
    if (value >= 10000) return `${(value / 10000).toFixed(0)}만`;
    return String(value);
  };

  return (
    <div style={{ maxWidth: 1200, margin: "24px auto", padding: "0 16px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>📊 통계 대시보드</h2>
        
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <select value={period} onChange={(e) => setPeriod(e.target.value as any)} style={{ fontSize: 13 }}>
            <option value="month">월간</option>
            <option value="quarter">분기</option>
            <option value="year">연간</option>
          </select>
          
          <select value={year} onChange={(e) => setYear(Number(e.target.value))} style={{ fontSize: 13 }}>
            {[2024, 2025, 2026, 2027].map((y) => (
              <option key={y} value={y}>{y}년</option>
            ))}
          </select>
          
          {period === "month" && (
            <select value={month} onChange={(e) => setMonth(Number(e.target.value))} style={{ fontSize: 13 }}>
              {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
                <option key={m} value={m}>{m}월</option>
              ))}
            </select>
          )}
          
          <button onClick={loadData} style={{ fontSize: 13 }}>새로고침</button>
          <button onClick={exportExcel} style={{ fontSize: 13 }}>Excel 내보내기</button>
        </div>
      </div>

      {/* 통계 카드 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16, marginBottom: 24 }}>
        <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, background: "#f9fafb" }}>
          <div style={{ fontSize: 13, color: "#888", marginBottom: 4 }}>총 고객</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: "#2563eb" }}>{summary.customers.total}명</div>
          <div style={{ fontSize: 12, color: "#555", marginTop: 4 }}>
            이번 기간 신규 {summary.customers.new_this_period}명
          </div>
        </div>
        
        <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, background: "#f9fafb" }}>
          <div style={{ fontSize: 13, color: "#888", marginBottom: 4 }}>활성 계약</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: "#16a34a" }}>{summary.policies.active}건</div>
          <div style={{ fontSize: 12, color: "#555", marginTop: 4 }}>
            전체 {summary.policies.total}건
          </div>
        </div>
        
        <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, background: "#f9fafb" }}>
          <div style={{ fontSize: 13, color: "#888", marginBottom: 4 }}>월 총보험료</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: "#ca8a04" }}>
            {formatCurrency(summary.policies.total_monthly_premium)}원
          </div>
          <div style={{ fontSize: 12, color: "#555", marginTop: 4 }}>
            평균 {formatCurrency(summary.policies.avg_premium)}원
          </div>
        </div>
        
        <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, background: "#f9fafb" }}>
          <div style={{ fontSize: 13, color: "#888", marginBottom: 4 }}>대기 상담</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: "#dc2626" }}>
            {summary.consultations.pending_followups}건
          </div>
          <div style={{ fontSize: 12, color: "#555", marginTop: 4 }}>
            이번 기간 {summary.consultations.this_period}건
          </div>
        </div>
      </div>

      {/* 월별 추이 */}
      {trendData.length > 0 && (
        <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, marginBottom: 24 }}>
          <h3 style={{ margin: "0 0 16px", fontSize: 16 }}>📈 월별 추이 ({year}년)</h3>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={trendData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="month" style={{ fontSize: 12 }} />
              <YAxis style={{ fontSize: 12 }} />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="신규고객" stroke="#2563eb" strokeWidth={2} />
              <Line type="monotone" dataKey="신규계약" stroke="#16a34a" strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* 지역별 분포 & 보험사별 계약 */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 24 }}>
        {/* 지역별 파이 차트 */}
        <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16 }}>
          <h3 style={{ margin: "0 0 16px", fontSize: 16 }}>🍰 지역별 분포</h3>
          {regionalData.length > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <PieChart>
                <Pie
                  data={regionalData}
                  cx="50%"
                  cy="50%"
                  labelLine={false}
                  label={(entry: any) => `${entry.name} ${(entry.percent * 100).toFixed(0)}%`}
                  outerRadius={80}
                  fill="#8884d8"
                  dataKey="value"
                >
                  {regionalData.map((_, index) => (
                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <p style={{ color: "#888", fontSize: 13 }}>데이터 없음</p>
          )}
        </div>

        {/* 보험사별 바 차트 */}
        <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16 }}>
          <h3 style={{ margin: "0 0 16px", fontSize: 16 }}>📊 보험사별 계약</h3>
          {insurerData.length > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={insurerData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" style={{ fontSize: 11 }} />
                <YAxis style={{ fontSize: 12 }} />
                <Tooltip />
                <Legend />
                <Bar dataKey="계약수" fill="#2563eb" />
                <Bar dataKey="월보험료" fill="#16a34a" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p style={{ color: "#888", fontSize: 13 }}>데이터 없음</p>
          )}
        </div>
      </div>

      {/* 상담 채널 분포 */}
      {Object.keys(summary.consultations.channels).length > 0 && (
        <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16 }}>
          <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>📞 상담 채널별 분포</h3>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
            {Object.entries(summary.consultations.channels).map(([channel, count]) => (
              <div key={channel} style={{ flex: "1 1 120px", textAlign: "center" }}>
                <div style={{ fontSize: 24, fontWeight: 700, color: "#2563eb" }}>{count}</div>
                <div style={{ fontSize: 13, color: "#888" }}>{channel}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
