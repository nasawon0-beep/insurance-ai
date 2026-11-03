-- [읽기 전용] migration(+seed_owner) 적용 후 검증 — Supabase SQL Editor에서 실행
-- 모든 행 result = 'OK' 여야 한다. 'FAIL' 이 하나라도 있으면 다음 단계 중지 후 보고.
-- owner_rows 는 seed_owner 실행 전이면 0(INFO), 실행 후 1 이어야 함.
with
t(name) as (values ('moadoom_member_roles'), ('moadoom_subscriptions'), ('moadoom_trial_grants'),
                   ('moadoom_subscription_audit'), ('moadoom_settings'), ('moadoom_email_outbox')),
member_rpc(sig) as (values ('public.moadoom_my_entitlement()'),
                           ('public.moadoom_admin_search_members(text)'),
                           ('public.moadoom_admin_member_detail(uuid)'),
                           ('public.moadoom_admin_change_subscription(uuid,text,date,integer,text)'),
                           ('public.moadoom_admin_settings()'),
                           ('public.moadoom_admin_set_expiry_notice_days(integer[])')),
internal_fn(sig) as (values ('public.moadoom_setting(text)'), ('public.moadoom_kst(timestamptz)'),
                            ('public.moadoom_subscription_state(uuid)'), ('public.moadoom_role_of(uuid)'),
                            ('public.moadoom_enqueue_email(uuid,text,text,jsonb)'),
                            ('public.moadoom_ensure_signup_trial(uuid)'),
                            ('public.moadoom_on_auth_user_confirmed()'),
                            ('public.moadoom_require_role(text[])'),
                            ('public.moadoom_enqueue_expiry_notices()'),
                            ('public.moadoom_mail_claim(integer)'),
                            ('public.moadoom_mail_result(bigint,boolean,text,text)')),
checks(check_name, detail, ok) as (
  select 'tables_exist', (select count(*) from pg_tables where schemaname = 'public' and tablename in (select name from t))::text,
         (select count(*) from pg_tables where schemaname = 'public' and tablename in (select name from t)) = 6
  union all
  select 'rls_enabled_' || name, c.relrowsecurity::text, c.relrowsecurity
  from t join pg_class c on c.relname = t.name join pg_namespace n on n.oid = c.relnamespace and n.nspname = 'public'
  union all
  select 'no_client_write_' || name || '_' || r, 'insert/update/delete',
         not (has_table_privilege(r, 'public.' || name, 'INSERT') or has_table_privilege(r, 'public.' || name, 'UPDATE')
              or has_table_privilege(r, 'public.' || name, 'DELETE'))
  from t cross join (values ('anon'), ('authenticated')) roles(r)
  union all
  select 'no_anon_read_' || name, 'select', not has_table_privilege('anon', 'public.' || name, 'SELECT') from t
  union all
  select 'authenticated_read_only_self_tables_' || name, 'select',
         has_table_privilege('authenticated', 'public.' || name, 'SELECT') = (name in ('moadoom_member_roles', 'moadoom_subscriptions'))
  from t
  union all
  select 'rpc_authenticated_' || sig, 'execute', has_function_privilege('authenticated', sig, 'EXECUTE') from member_rpc
  union all
  select 'rpc_no_anon_' || sig, 'execute', not has_function_privilege('anon', sig, 'EXECUTE') from member_rpc
  union all
  select 'internal_no_client_' || sig || '_' || r, 'execute', not has_function_privilege(r, sig, 'EXECUTE')
  from internal_fn cross join (values ('anon'), ('authenticated')) roles(r)
  union all
  select 'definer_search_path_fixed', (select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
           where n.nspname = 'public' and p.proname like 'moadoom\_%' and p.prosecdef
             and not exists (select 1 from unnest(coalesce(p.proconfig, '{}')) x where x like 'search_path=%'))::text || ' missing',
         not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
           where n.nspname = 'public' and p.proname like 'moadoom\_%' and p.prosecdef
             and not exists (select 1 from unnest(coalesce(p.proconfig, '{}')) x where x like 'search_path=%'))
  union all
  select 'trigger_on_auth_users', (select count(*) from pg_trigger where tgname = 'moadoom_auth_user_confirmed' and not tgisinternal)::text,
         (select count(*) from pg_trigger where tgname = 'moadoom_auth_user_confirmed' and not tgisinternal) = 1
  union all
  select 'setting_trial_days', (select value::text from public.moadoom_settings where key = 'trial_days'),
         (select value from public.moadoom_settings where key = 'trial_days') = '7'::jsonb
  union all
  select 'setting_expiry_notice_days', (select value::text from public.moadoom_settings where key = 'expiry_notice_days'),
         (select jsonb_typeof(value) from public.moadoom_settings where key = 'expiry_notice_days') = 'array'
  union all
  select 'lifetime_check_constraint', 'kind=lifetime <=> ends_at null',
         exists (select 1 from pg_constraint where conrelid = 'public.moadoom_subscriptions'::regclass and contype = 'c'
                 and pg_get_constraintdef(oid) like '%lifetime%')
  union all
  select 'admin_role_count', (select count(*) from public.moadoom_member_roles where role = 'admin')::text,
         (select count(*) from public.moadoom_member_roles where role = 'admin') = 0
  union all
  select 'owner_rows', (select count(*) from public.moadoom_member_roles where role = 'owner')::text,
         (select count(*) from public.moadoom_member_roles where role = 'owner') <= 1
)
select check_name, detail, case when ok then 'OK' else 'FAIL' end as result
from checks
order by result, check_name;
