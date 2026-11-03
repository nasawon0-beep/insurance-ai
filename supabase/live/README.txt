모아둠 실제 Supabase 적용 순서 (팀장 실행용, project ref fhdushntttxbfwkxylhz)
모든 경로는 C:/build/moadoom-central-auth 기준. SQL은 Supabase 대시보드 → SQL Editor → New query에 파일 내용 전체를 붙여 실행.

[읽기 전용] 1. supabase/live/00_precheck_readonly.sql
   기대: moadoom_objects_existing=0, auth_users_triggers_moadoom=0, roles_present에 anon/authenticated/service_role 포함.
   auth_users_confirmed / *_oldest_days / *_within_7d 값 → 기존 계정 소급 체험 영향 확인용(이메일 출력 없음).
[읽기 전용] 2. supabase/live/05_owner_lookup_readonly.sql (이메일 자리만 바꿔 실행) → 상원님 UID, email_confirmed=true 확인.
[쓰기]     3. supabase/migrations/20261103000000_moadoom_central_entitlements.sql
   begin/commit 한 트랜잭션. 오류가 나면 전체 롤백(부분 적용 없음) — 오류 문구 그대로 보고.
[읽기 전용] 4. supabase/live/30_postcheck_readonly.sql → 모든 행 OK (owner_rows=0 은 정상, 다음 단계 전)
[쓰기]     5. supabase/seed_owner.sql — 00000000-0000-0000-0000-000000000000 을 2번 UID로 바꿔 실행 → 1행 반환
[읽기 전용] 6. supabase/live/30_postcheck_readonly.sql 재실행 → 모두 OK, owner_rows=1
[CLI]      7. Edge Function 서명키·배포 (브라우저에 개인키 입력 없음, 아래 순서)
[대시보드] 8. Authentication → URL Configuration / Emails 템플릿 (결과 문서 4절)
[되돌리기] supabase/rollback/20261103000000_moadoom_central_entitlements.down.sql
   (moadoom_ 객체와 auth.users 트리거만 제거, 기존 Auth 계정 행 보존. 체험 1회 기록도 함께 삭제됨)

7. Edge Function (팀장 PC 터미널, 대시보드 화면에 비밀값 입력 없음)
   a) npx supabase@latest login                         # 브라우저 OAuth 로그인 — 상원님 인증이 필요한 유일한 시점일 수 있음
   b) 개인키를 화면에 내지 않고 env 파일로만 생성 (출력은 kid와 공개키뿐):
      echo {"op":"keygen_env","env_out":"C:/secure/moadoom-license.env","kid":"moadoom-20261103"} | node --experimental-strip-types supabase/tools/license_cli.ts
      → 출력 public_key 는 앱 빌드 VITE_MOADOOM_LICENSE_PUBLIC_KEYS={"moadoom-20261103":"<public_key>"} 에 사용(공개값)
   c) npx supabase@latest secrets set --project-ref fhdushntttxbfwkxylhz --env-file C:/secure/moadoom-license.env
   d) npx supabase@latest functions deploy moadoom-license --project-ref fhdushntttxbfwkxylhz --no-verify-jwt
      (함수가 auth.getUser로 직접 토큰을 검증함. 게이트웨이 JWT 검사를 끄는 이유: 앱 WebView의 CORS 사전요청(OPTIONS)과 새 키 체계 호환)
   e) env 파일: 키 분실 시 모든 PC가 재서명 필요하므로 오프라인 보관 위치를 정하고, 작업 폴더에서는 삭제.
   moadoom-mailer 는 메일 API 확보 전 배포하지 않음.
