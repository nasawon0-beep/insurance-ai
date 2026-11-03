-- [읽기 전용] 모아둠 migration 적용 전 점검 — Supabase SQL Editor에서 그대로 실행
-- 쓰기 없음. 이메일·비밀값을 출력하지 않는다(개수·시각 범위만).
-- 기대값: moadoom_objects_existing=0, auth_users_triggers_moadoom=0, roles_present=anon,authenticated,service_role,
--         server_version_num >= 150000
select 'server_version_num' as check_name, current_setting('server_version_num') as value
union all
select 'current_user', current_user::text
union all
select 'roles_present', (select string_agg(rolname, ',' order by rolname) from pg_roles
                         where rolname in ('anon', 'authenticated', 'service_role', 'supabase_auth_admin'))
union all
select 'moadoom_objects_existing', (
  (select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relname like 'moadoom\_%')
  + (select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname like 'moadoom\_%'))::text
union all
select 'public_tables_total', (select count(*) from pg_tables where schemaname = 'public')::text
union all
select 'auth_users_total', (select count(*) from auth.users)::text
union all
select 'auth_users_confirmed', (select count(*) from auth.users where email_confirmed_at is not null)::text
union all
select 'auth_users_confirmed_oldest_days',
  coalesce((select floor(extract(epoch from now() - min(email_confirmed_at)) / 86400)::text
            from auth.users where email_confirmed_at is not null), '-')
union all
select 'auth_users_confirmed_within_7d',
  (select count(*) from auth.users where email_confirmed_at > now() - interval '7 days')::text
union all
select 'auth_users_triggers_total', (select count(*) from pg_trigger t join pg_class c on c.oid = t.tgrelid
    join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'auth' and c.relname = 'users' and not t.tgisinternal)::text
union all
select 'auth_users_triggers_moadoom', (select count(*) from pg_trigger t join pg_class c on c.oid = t.tgrelid
    join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'auth' and c.relname = 'users' and t.tgname like 'moadoom\_%')::text;
