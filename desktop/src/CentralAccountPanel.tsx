import MaintenancePanel from './MaintenancePanel';
import { openUrl } from "@tauri-apps/plugin-opener";
import { getAuthProvider } from "./auth";
import { CENTRAL_ADMIN_URL, centralEnabled } from "./centralConfig";
import { isOperator, type LicenseState } from "./licenseStatus";

export default function CentralAccountPanel({ lic }: { lic: (LicenseState & { offline?: boolean }) | null }) {
  if (!centralEnabled()) return null;
  return <div style={{ marginTop: 8 }}>
    <p>로그인 방식: 모아둠 계정</p>
    {getAuthProvider() === "central" && isOperator(lic) && !lic?.offline &&
      <button onClick={() => void openUrl(CENTRAL_ADMIN_URL)}>회원 이용권 관리 열기</button>}
    <p>이전 자료는 소유권 확인 없이 현재 계정에 자동 연결하지 않습니다.</p>
    <MaintenancePanel/>
  </div>;
}
