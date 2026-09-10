# shellcheck shell=bash
# 공용: 로컬 서버 포트 상수 + control-server 시크릿 부트스트랩.
# start.command / start-servers.command / stop.command 에서 `. "$HERE/lib/servers.sh"` 로 source.
# 포트는 desktop/src/config.ts 의 URL 과 같은 값이어야 한다.

ENGINE_PORT=8420    # local-engine  — 고객 데이터 / 검색 / 녹취
CONTROL_PORT=8790   # control-server — 로그인 / 라이선스 / 기기

# control-server 시크릿을 환경에 로드한다. 필요 시(최초 1회) 파일을 생성한다.
#   $1 = repo ROOT
#   $2 = 토큰 생성용 python 실행 경로
#   $3 = "create" 이면 없을 때 생성, 아니면 없으면 아무것도 안 하고 1 반환
# 성공(로드됨) 0 / 시크릿 파일 없음 1
load_control_secrets() {
  local root="$1" py="$2" mode="${3:-}"
  local secrets="$root/control-server/secrets.env"
  if [ ! -f "$secrets" ] && [ "$mode" = "create" ]; then
    echo "control-server 시크릿 생성 (최초 1회): $secrets" >&2
    local jwt adm
    jwt=$("$py" -c 'import secrets;print(secrets.token_hex(32))')
    adm=$("$py" -c 'import secrets;print(secrets.token_hex(32))')
    ( umask 177; cat > "$secrets" <<EOF
# 이 PC 전용. 커밋·공유 금지. 삭제하면 다음 실행에 재생성됨(기존 로그인 토큰 무효).
CONTROL_JWT_SECRET=$jwt
CONTROL_ADMIN_TOKEN=$adm
# 데스크톱 빌드의 VITE_LICENSE_SECRET 과 반드시 동일해야 오프라인 라이선스(P0-B)가 검증됨.
# 파일럿은 둘 다 아래 기본값. 정식 배포 시 양쪽을 같은 강한 값으로 교체.
CONTROL_LICENSE_SECRET=dev-license-secret-change-me
EOF
    )
    chmod 600 "$secrets"
  fi
  [ -f "$secrets" ] || return 1
  set -a; . "$secrets"; set +a
  export CONTROL_LOCAL_RECOVERY=1   # 로컬 파일럿: 로그인 전 계정 복구 허용 (loopback 게이트가 원격 차단)
  return 0
}
