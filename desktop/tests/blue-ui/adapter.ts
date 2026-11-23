// Offline adapter for the real production App. Synthetic, non-identifying data only.
// It replaces engine/auth/native modules at bundle time; it is never imported by production code.
const qs = new URLSearchParams(location.search);
export const scenario = qs.get('scenario') ?? 'default';

const SURNAMES = ['가', '나', '다', '라', '마', '바', '사', '아', '자', '차'];
function mkCustomer(i: number, long = false) {
  const name = long
    ? `합성고객 ${SURNAMES[i % 10]}${'가나다라마바사아자차'.repeat(3)} 긴이름 확인용`
    : `합성고객 ${i + 1}`;
  return {
    id: `fx-${i + 1}`, name, phone: `010-0000-${String(1000 + i)}`, email: `fx${i + 1}@example.invalid`,
    rrn: null, rrn_masked: null, has_rrn: false, birth_date: '1986-03-14', gender: i % 2 ? 'F' : 'M',
    address: '합성시 합성구 예시로 1', occupation: '가상 직업', tags: ['가상 데이터'], memo: '촬영용 합성 메모입니다.',
    policy_count: i % 4, active_policy_count: i % 4, own_policy_count: i % 4,
    customer_status: i % 5 === 3 ? '가망' : '가입', effective_status: i % 5 === 3 ? '가망' : '가입',
    created_at: '2026-01-01', updated_at: '2026-01-01', first_registered_ym: '2026-01', soonest_expiry: null,
    next_follow_up: null, last_consulted_at: '2026-01-15',
  };
}
const count = scenario === 'empty' ? 0 : scenario === 'many' ? 48 : 6;
export const customers = Array.from({ length: count }, (_, i) => mkCustomer(i, scenario === 'long' && i === 0));

const coverageDefs: [string, string, number, number][] = [
  ['암', '일반암 진단비', 30000000, 50000000], ['뇌혈관질환', '뇌혈관질환 진단비', 20000000, 20000000],
  ['심장질환', '허혈성심장질환 진단비', 20000000, 20000000], ['입원비(일당)·치료비', '질병 입원일당', 30000, 50000],
];
export function ledger(id: string, pid: string) {
  return coverageDefs.map(([g, n, a], i) => ({
    id: `${pid}-cov-${i}`, customer_id: id, policy_id: pid, source_document_id: `${id}-doc`, source_page: i + 1,
    insurer: '가상보험사', product_name: '합성 보장 예시', rider_name: n, standard_name: n, coverage_type: g, amount: a,
    amount_text: `${a.toLocaleString()}원`, amount_status: 'confirmed', link_status: 'linked',
    start_date: '2026-01-01', end_date: '2046-01-01', document_status: 'CONFIRMED',
  }));
}
export function policies(id: string) {
  const n = scenario === 'many' ? 12 : 3;
  return Array.from({ length: n }, (_, k) => {
    const pid = `${id}-p${k}`;
    return {
      id: pid, customer_id: id, insurer: ['가상생명', '가상해상', '가상화재'][k % 3],
      product_name: k === 0 ? '무배당 안심생활 건강종합보험 갱신형 진단·수술 보장 플랜 장기 상품명 줄바꿈 확인' : `합성 상품 ${k + 1}`,
      policy_number: `FX-${1000 + k}`, premium: 30000 + k * 5000, payment_cycle: 'monthly',
      start_date: '2026-01-01', end_date: '2046-01-01', status: 'ACTIVE', is_own: 1, document_status: 'CONFIRMED',
      coverage_count: 4, coverages: ledger(id, pid),
    };
  });
}
export function consultations(id: string) {
  return [{ id: `${id}-c1`, customer_id: id, customer_name: customers.find((c) => c.id === id)?.name, consulted_at: '2026-01-15', channel: '방문', title: '보장 점검 상담 예시', content: '합성 상담 기록입니다.', follow_up_at: null, follow_up_done_at: null, coverage_json: null }];
}
export function analysis(id: string) {
  return {
    customer_id: id, run_id: `${id}-run`, status: 'completed', doc_type: 'coverage_analysis', audience: 'internal',
    summary: { overall: '합성 보장분석 예시 · 실제 분석 아님', total_items: 4, displayed_items: 4 },
    categories: coverageDefs.map(([g, n, cur, rec], i) => ({
      category_id: `cat-${i}`, category_group: g, status_summary: cur < rec ? '부족' : '충분', max_priority: 'medium',
      items: [{ id: `${id}-it-${i}`, source_index: i, category_group: g, coverage_name: n, status: cur < rec ? '부족' : '충분', current_amount: cur, recommended_amount: rec, gap_amount: rec - cur, current_text: `${cur.toLocaleString()}원`, recommended_text: `${rec.toLocaleString()}원`, amount_status: 'confirmed', priority: 'medium', customer_summary: '예시 기준 금액이며 추천이 아닙니다.', matched_policy_coverage_ids: [`${id}-p0-cov-${i}`] }],
    })),
    unclassified_policy_coverages: [], unlinked_policy_coverages: [], review_required_policy_coverages: [],
  };
}
function json(body: unknown, init: ResponseInit = {}) { return new Response(JSON.stringify(body), { ...init, headers: { 'Content-Type': 'application/json' } }); }
export const requests: { path: string; method: string }[] = [];
export async function engineFetch(path: string, init: RequestInit = {}) {
  const method = (init.method || 'GET').toUpperCase();
  requests.push({ path, method });
  // Yield a macrotask so a hypothetical fetch/render loop can never starve the browser (and is visible in the audit log).
  await new Promise((r) => setTimeout(r, 4));
  if (method !== 'GET') return json({ detail: '시안 검증용 읽기 전용 fixture입니다. 저장은 수행하지 않습니다.' }, { status: 403 });
  if (!path.startsWith('/') || path.startsWith('//')) return json({ detail: '외부 연결 금지' }, { status: 403 });
  const u = new URL(path, 'https://fixture.invalid'); const p = u.pathname; let body: any;
  if (p === '/health') body = { local_engine: 'ok', status: 'ok' };
  else if (p === '/diagnostics') body = { engine: { local_engine: 'fixture', ollama: 'disconnected', models: [] }, customer_db: { encryption: 'fixture', key_source: 'fixture' }, whisper: { model: '-', backend: '-', device: '-', compute_type: '-', downloaded: false }, ocr: { backend: 'none' }, data: { customers: customers.length, policies: 3, active_policies: 3, consultations: 3 }, rag: { docs: [], total_chunks: 0 } };
  else if (p === '/backups') body = { backups: [] };
  else if (p === '/exports') body = { exports: [] };
  else if (p === '/business/reset/status') body = { sync_paused: false, restore_candidate: null, last_restore_safety_copy: null };
  else if (p === '/usage/logs') body = { logs: [] };
  else if (p === '/dashboard') body = { counts: { customers: customers.length, policies: 3, active_policies: 3, consultations: 3 }, follow_ups: [], expiring_policies: [], birthdays: [], recent_consultations: customers.slice(0, 3).flatMap((c) => consultations(c.id)) };
  else if (p === '/customers') {
    const q = u.searchParams.get('q') || '';
    body = { customers: customers.filter((c) => c.name.includes(q) && (!u.searchParams.get('status') || u.searchParams.get('status') === c.effective_status)) };
  } else if (p === '/tags') body = { tags: ['가상 데이터'] };
  else if (p === '/coverage-analysis-runs') body = { runs: customers.map((c) => ({ id: `${c.id}-run`, customer_id: c.id, customer_name: c.name })) };
  else if (p === '/management') body = { expiring_policies: [], birthdays: [] };
  else {
    const m = p.match(/^\/customers\/(fx-\d+)(?:\/(.*))?$/);
    if (m) {
      const id = m[1], tail = m[2];
      if (tail === 'audit') body = [];
      else if (!tail) body = { ...customers.find((c) => c.id === id), policies: policies(id), consultations: consultations(id) };
      else if (tail === 'coverage-analysis') body = analysis(id);
      else if (tail === 'policy-coverages') body = { policy_coverages: policies(id).flatMap((x) => x.coverages) };
      else if (tail === 'family-relationships') body = { relationships: [] };
      else if (tail === 'policies') body = { policies: policies(id) };
      else if (tail === 'consultations') body = { consultations: consultations(id) };
    }
  }
  if (body === undefined) return json({ detail: 'fixture에 포함되지 않은 조회입니다.' }, { status: 404 });
  return json(body);
}
export const ensureLocalEngineRecovered = async () => undefined;
export const activateAccountWorkspace = async () => undefined;
export const LOCAL_ENGINE_URL = 'http://127.0.0.1:0';
export const track = () => {};
export const IS_NATIVE_RUNTIME = false, QA_MODE = false, QA_INSTANCE_ID = '', QA_PROFILE_FINGERPRINT = '';
export async function initializeRuntimeProfile() {}
export async function invoke(command: string, args?: any): Promise<any> {
  if (command === 'local_engine_request') {
    const r = await engineFetch(args?.request?.path ?? '/health', { method: args?.request?.method ?? 'GET' });
    const body = args?.request?.path === '/health' ? JSON.stringify({ local_engine: 'ok' }) : await r.text();
    return { status: r.status, headers: [], body: Array.from(new TextEncoder().encode(body)) };
  }
  if (command === 'local_engine_log_path' || command === 'start_local_engine') return 'fixture.log';
  if (command === 'mobile_sync_status') return { running: false, starting: false, url: null, host: null, port: 0, certificateFingerprint: null, certificateExpiresAt: null, lastError: null };
  if (command === 'mobile_sync_list_devices') return [];
  if (command === 'mobile_sync_startup_preference') return { restoreOnAppStart: false };
  if (command === 'maintenance_status') return { available: false, message: 'fixture · 유지보수 미연결', paused: false };
  throw new Error('fixture: 네이티브 작업 미지원');
}
export const revealItemInDir = invoke;
export const centralEnabled = () => true;
export const CENTRAL_SIGNUP_OPEN = false, CENTRAL_ADMIN_URL = '', CENTRAL_URL = '', CENTRAL_PUBLISHABLE_KEY = '';
export const loadRememberedEmail = () => '', saveRememberedEmail = () => {};
export const centralClient = () => { throw Error('fixture는 인증에 연결하지 않습니다'); };
export const authenticate = centralClient, requestCentralPasswordReset = centralClient, changePassword = centralClient, removeDevice = centralClient, installUpdate = centralClient, openUrl = centralClient;
export const isSignedIn = () => true;
export const getUser = () => ({ email: 'fixture@example.invalid' });
export const getAuthProvider = () => 'central';
export const rrnEnabled = () => false;
export const loadEngineSettings = async () => undefined;
export const logout = async () => undefined;
export const resolveLicense = async () => null, listDevices = async () => [], fetchMe = async () => null;
export const resolveLicenseStatus = async () => ({ kind: 'ok', offline: false, state: { plan: 'fixture', device_limit: 1, expiry: null, status: 'active', days_left: null, role: 'owner', effective_status: 'active', kind: 'lifetime' } });
export const resolveLegacyReadonlySession = async () => ({ kind: 'message', message: 'fixture' });
export const checkForUpdate = async () => ({ kind: 'up-to-date' });
export const CONTROL_URL = 'http://127.0.0.1:0';
export const listen = async (_event?: string, _cb?: unknown) => () => {};
export type UnlistenFn = () => void;
