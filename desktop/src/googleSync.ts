/**
 * Google Workspace 연동 API
 */
import { engineFetch } from "./engine";

export interface Customer {
  db_type?: string;
  date?: string;
  region?: string;
  call_duration?: string;
  visit_condition?: string;
  name?: string;
  phone?: string;
  address?: string;
  birth_date?: string;
  gender?: string;
  memo?: string;
}

export interface VisitCustomer {
  name: string;
  birth_year: string;  // 2자리
  region: string;
  address: string;
  phone?: string;
  birth_date?: string;
  premium?: string;
  memo?: string;
}

/**
 * 고객 데이터를 Google Sheets에 동기화
 */
export async function syncToSheets(
  customers: Customer[],
  sheetName?: string
): Promise<{ success: boolean; appended_rows: number; spreadsheet_url: string; sheet_name: string }> {
  const response = await engineFetch("/google/sheets/sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ customers, sheet_name: sheetName }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Sheets 동기화 실패");
  }

  return await response.json();
}

/**
 * Google Sheets에서 데이터 읽기
 */
export async function readFromSheets(
  rangeName: string = "2026-09!A:K"
): Promise<{ success: boolean; row_count: number; data: string[][] }> {
  const response = await engineFetch(
    `/google/sheets/read?range_name=${encodeURIComponent(rangeName)}`
  );

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Sheets 읽기 실패");
  }

  return await response.json();
}

/**
 * Google Calendar에 방문 일정 생성
 */
export async function createCalendarEvent(
  customer: VisitCustomer,
  visitDatetime: string  // ISO 8601 형식
): Promise<{ success: boolean; event_id: string; event_url: string }> {
  const response = await engineFetch("/google/calendar/event", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ customer, visit_datetime: visitDatetime }),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Calendar 일정 생성 실패");
  }

  return await response.json();
}

/**
 * 앞으로 N일 이내 방문 일정 조회
 */
export async function getUpcomingVisits(
  days: number = 7
): Promise<{ success: boolean; event_count: number; events: any[] }> {
  const response = await engineFetch(`/google/calendar/upcoming?days=${days}`);

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "일정 조회 실패");
  }

  return await response.json();
}
