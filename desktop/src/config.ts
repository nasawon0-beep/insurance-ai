// 로컬 서버 주소 단일 정의. 포트는 scripts/lib/servers.sh 와 같은 값이어야 한다.
//   - local-engine : 고객 데이터 / 검색 / 녹취   (:8420)
//   - control-server: 로그인 / 라이선스 / 기기    (:8790)

export const LOCAL_ENGINE_URL = "http://127.0.0.1:8420";

export const CONTROL_URL =
  (import.meta as any).env?.VITE_CONTROL_URL || "http://127.0.0.1:8790";
