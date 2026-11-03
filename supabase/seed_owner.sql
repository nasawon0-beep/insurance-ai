-- owner 초기 지정 (프로젝트 소유자가 Supabase 대시보드 SQL Editor에서 1회 실행)
-- 1) 상원님이 사이트에서 가입하고 확인 메일을 눌러 이메일 확인을 마친다.
-- 2) Authentication → Users 에서 본인 계정의 UID(UUID)를 확인해 아래 자리에 넣는다.
--    (이메일 문자열로 자동 승격하지 않는다 — UUID를 사람이 확인해 지정한다.)
-- 3) 실행 후 결과 1행과 email_confirmed=true 를 확인한다.
insert into public.moadoom_member_roles (user_id, role, granted_by)
select u.id, 'owner', 'dashboard-sql'
from auth.users u
where u.id = '00000000-0000-0000-0000-000000000000'::uuid  -- ← 확인한 UID로 교체
  and u.email_confirmed_at is not null
on conflict (user_id) do update set role = 'owner', granted_at = now(), granted_by = 'dashboard-sql'
returning user_id, role, granted_at;
