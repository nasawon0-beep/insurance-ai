export const MAINTENANCE_LIMIT = '복구키는 현재 Windows 사용자 DPAPI로 이 설치에만 보존됩니다. Windows 프로필 또는 설치 보호정보 손실 시 복구를 보장하지 않습니다. 클라우드 보관·평문 키 내보내기는 제공하지 않습니다.';
export const maintenanceAllowed = (available: boolean, prepared: boolean, confirmed: boolean, busy: boolean) => available && prepared && confirmed && !busy;
