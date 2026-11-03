-- 롤백: 20261103000000_moadoom_central_entitlements.sql 가 만든 객체만 제거한다.
-- auth.users(회원 계정)는 건드리지 않는다(트리거만 제거). 실행 전 감사/체험지급/발송 기록이 필요하면 먼저 내보낼 것:
--   copy (select * from public.moadoom_subscription_audit) to stdout with csv header;  (SQL Editor에서는 결과 다운로드)
-- 주의: moadoom_trial_grants를 지우면 "1계정 1회 무료체험" 기록도 사라진다. 재적용 시 기존 확인 회원이 다시 체험을 받을 수 있다.
begin;
drop trigger if exists moadoom_auth_user_confirmed on auth.users;
drop function if exists public.moadoom_on_auth_user_confirmed();
drop function if exists public.moadoom_mail_result(bigint, boolean, text, text);
drop function if exists public.moadoom_mail_claim(integer);
drop function if exists public.moadoom_enqueue_expiry_notices();
drop function if exists public.moadoom_admin_set_expiry_notice_days(integer[]);
drop function if exists public.moadoom_admin_settings();
drop function if exists public.moadoom_admin_change_subscription(uuid, text, date, integer, text);
drop function if exists public.moadoom_admin_member_detail(uuid);
drop function if exists public.moadoom_admin_search_members(text);
drop function if exists public.moadoom_require_role(text[]);
drop function if exists public.moadoom_my_entitlement();
drop function if exists public.moadoom_ensure_signup_trial(uuid);
drop function if exists public.moadoom_enqueue_email(uuid, text, text, jsonb);
drop function if exists public.moadoom_role_of(uuid);
drop function if exists public.moadoom_subscription_state(uuid);
drop function if exists public.moadoom_kst(timestamptz);
drop function if exists public.moadoom_setting(text);
drop table if exists public.moadoom_email_outbox;
drop table if exists public.moadoom_settings;
drop table if exists public.moadoom_subscription_audit;
drop table if exists public.moadoom_trial_grants;
drop table if exists public.moadoom_subscriptions;
drop table if exists public.moadoom_member_roles;
commit;
