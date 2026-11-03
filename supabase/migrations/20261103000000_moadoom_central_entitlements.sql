-- 모아둠 중앙 회원 이용권(entitlement)
-- 범위: 회원 로그인(Supabase Auth)과 이용권/안내메일 메타데이터만. 보험 고객 DB/PDF는 저장하지 않는다.
-- 원칙:
--   * 운영 역할(owner/admin)과 이용권(subscription)을 분리한다. 무기한 무료(lifetime)는 이용권 종류이지 역할이 아니다.
--   * 클라이언트(anon/authenticated)는 테이블에 직접 쓰지 못한다(RLS + 권한 회수).
--   * 이용권 변경은 SECURITY DEFINER RPC에서 auth.uid()가 owner인지 서버가 확인한 뒤에만 수행한다.
--   * 역할 부여 RPC는 없다. owner 지정은 대시보드 SQL(프로젝트 소유자)로만 한다(seed_owner.sql).
--   * 시각은 서버 now()(timestamptz) 기준. 기간 종료는 정확한 종료 시각(ends_at)으로 판정한다.
--   * 무료체험 7일: 이메일 확인 완료 시각이 시작점. 1계정 1회(이메일 해시 기준 포함), 재로그인/재설정/재설치로 초기화되지 않는다.
--   * 결제/요금제는 없다. kind/plan 칼럼은 나중의 유료 플랜 연결을 위한 자리다.
--   * 모든 객체 이름에 moadoom_ 접두사를 쓴다(같은 프로젝트의 다른 앱과 분리).

begin;

-- ---------------------------------------------------------------- 테이블
create table if not exists public.moadoom_member_roles (
  user_id    uuid primary key references auth.users(id) on delete cascade,
  role       text not null check (role in ('owner', 'admin')),
  granted_at timestamptz not null default now(),
  granted_by text not null
);

create table if not exists public.moadoom_subscriptions (
  user_id        uuid primary key references auth.users(id) on delete cascade,
  kind           text not null check (kind in ('trial', 'term', 'lifetime')),
  plan           text not null default 'FREE',      -- 향후 유료 플랜 식별자(현재 결제 없음)
  starts_at      timestamptz not null,
  ends_at        timestamptz,                       -- lifetime만 null(명시적 무기한). 2099 같은 가짜 날짜 금지
  suspended_at   timestamptz,
  updated_at     timestamptz not null default now(),
  check ((kind = 'lifetime') = (ends_at is null)),
  check (ends_at is null or ends_at > starts_at)
);

-- 무료체험 지급 기록: 구독 행이 바뀌거나 회원이 삭제·재가입해도 남는다(FK 없음, 이메일은 해시만).
create table if not exists public.moadoom_trial_grants (
  user_id     uuid primary key,
  email_hash  text not null unique,
  granted_at  timestamptz not null default now(),
  starts_at   timestamptz not null,
  ends_at     timestamptz not null
);

create table if not exists public.moadoom_subscription_audit (
  id             bigint generated always as identity primary key,
  at             timestamptz not null default now(),
  actor_user_id  uuid,                               -- null = 시스템(자동 무료체험)
  target_user_id uuid not null,
  action         text not null check (action in
                   ('trial_auto', 'trial', 'term', 'lifetime', 'adjust', 'suspend', 'resume')),
  before_state   jsonb,
  after_state    jsonb,
  reason         text not null check (length(btrim(reason)) > 0)
);
create index if not exists moadoom_subscription_audit_target_idx
  on public.moadoom_subscription_audit (target_user_id, at desc);

create table if not exists public.moadoom_settings (
  key        text primary key,
  value      jsonb not null,
  updated_at timestamptz not null default now(),
  updated_by uuid
);
insert into public.moadoom_settings (key, value) values
  ('trial_days', '7'::jsonb),
  ('expiry_notice_days', '[3, 1]'::jsonb)   -- 기본 제안: 종료 3일 전, 1일 전
on conflict (key) do nothing;

-- 안내 메일 발송함(outbox). 실제 발송은 moadoom-mailer 함수가 서비스 권한으로 처리한다.
create table if not exists public.moadoom_email_outbox (
  id                  bigint generated always as identity primary key,
  user_id             uuid not null,
  to_email            text not null,
  kind                text not null check (kind in ('welcome_trial', 'entitlement_changed', 'expiry_notice')),
  dedupe_key          text not null unique,            -- 같은 안내 중복 방지
  payload             jsonb not null default '{}'::jsonb,
  status              text not null default 'queued' check (status in ('queued', 'sending', 'sent', 'failed')),
  attempts            integer not null default 0,
  max_attempts        integer not null default 5,
  next_attempt_at     timestamptz not null default now(),
  last_error          text,
  provider_message_id text,
  created_at          timestamptz not null default now(),
  sent_at             timestamptz
);
create index if not exists moadoom_email_outbox_due_idx on public.moadoom_email_outbox (status, next_attempt_at);
create index if not exists moadoom_email_outbox_user_idx on public.moadoom_email_outbox (user_id, created_at desc);

alter table public.moadoom_member_roles enable row level security;
alter table public.moadoom_subscriptions enable row level security;
alter table public.moadoom_trial_grants enable row level security;
alter table public.moadoom_subscription_audit enable row level security;
alter table public.moadoom_settings enable row level security;
alter table public.moadoom_email_outbox enable row level security;

-- 직접 쓰기 금지(모든 클라이언트 역할). 본인 행 읽기만 허용.
revoke all on public.moadoom_member_roles, public.moadoom_subscriptions, public.moadoom_trial_grants,
  public.moadoom_subscription_audit, public.moadoom_settings, public.moadoom_email_outbox from anon, authenticated;
grant select on public.moadoom_member_roles to authenticated;
grant select on public.moadoom_subscriptions to authenticated;

drop policy if exists moadoom_roles_self_read on public.moadoom_member_roles;
create policy moadoom_roles_self_read on public.moadoom_member_roles
  for select to authenticated using (user_id = auth.uid());
drop policy if exists moadoom_subscriptions_self_read on public.moadoom_subscriptions;
create policy moadoom_subscriptions_self_read on public.moadoom_subscriptions
  for select to authenticated using (user_id = auth.uid());
-- trial_grants/audit/settings/outbox: 정책 없음 → RPC 밖에서는 읽기/쓰기 불가

-- ---------------------------------------------------------------- 내부 함수
create or replace function public.moadoom_setting(k text) returns jsonb
language sql stable security definer set search_path = public, pg_temp as $$
  select value from public.moadoom_settings where key = k
$$;
revoke all on function public.moadoom_setting(text) from public, anon, authenticated;

-- 화면 표시용(한국 표준시, 고정 +9). 판정은 timestamptz 비교로 한다.
create or replace function public.moadoom_kst(ts timestamptz) returns text
language sql immutable set search_path = pg_catalog as $$
  select to_char((ts at time zone 'UTC') + interval '9 hours', 'YYYY-MM-DD HH24:MI')
$$;
revoke all on function public.moadoom_kst(timestamptz) from public, anon, authenticated;

create or replace function public.moadoom_subscription_state(target uuid) returns jsonb
language sql stable security definer set search_path = public, pg_temp as $$
  select coalesce(
    (select jsonb_build_object(
        'kind', s.kind,
        'plan', s.plan,
        'starts_at', s.starts_at,
        'ends_at', s.ends_at,
        'suspended_at', s.suspended_at,
        'server_now', now(),
        'remaining_seconds', case when s.ends_at is null then null
                                  else greatest(0, floor(extract(epoch from (s.ends_at - now())))) end,
        'days_left', case when s.ends_at is null then null
                          else ceil(extract(epoch from (s.ends_at - now())) / 86400.0) end,
        'status', case
            when s.suspended_at is not null then 'suspended'
            when now() < s.starts_at then 'scheduled'
            when s.ends_at is not null and now() >= s.ends_at then 'expired'
            else 'active' end,
        'trial_started_at', (select t.starts_at from public.moadoom_trial_grants t where t.user_id = target))
     from public.moadoom_subscriptions s where s.user_id = target),
    jsonb_build_object('status', 'none', 'kind', null, 'plan', null, 'starts_at', null, 'ends_at', null,
                       'suspended_at', null, 'server_now', now(), 'remaining_seconds', null, 'days_left', null,
                       'trial_started_at', (select t.starts_at from public.moadoom_trial_grants t where t.user_id = target)))
$$;
revoke all on function public.moadoom_subscription_state(uuid) from public, anon, authenticated;

create or replace function public.moadoom_role_of(target uuid) returns text
language sql stable security definer set search_path = public, pg_temp as $$
  select role from public.moadoom_member_roles where user_id = target
$$;
revoke all on function public.moadoom_role_of(uuid) from public, anon, authenticated;

create or replace function public.moadoom_enqueue_email(target uuid, mail_kind text, dedupe text, data jsonb)
returns void
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare
  addr text;
begin
  select email into addr from auth.users where id = target;
  if addr is null then return; end if;
  insert into public.moadoom_email_outbox (user_id, to_email, kind, dedupe_key, payload)
  values (target, addr, mail_kind, dedupe, coalesce(data, '{}'::jsonb))
  on conflict (dedupe_key) do nothing;
end $$;
revoke all on function public.moadoom_enqueue_email(uuid, text, text, jsonb) from public, anon, authenticated;

-- 이메일 확인 완료 → 무료체험 자동 지급(1회). 트리거와 my_entitlement(자가 복구)에서 같은 함수를 쓴다.
create or replace function public.moadoom_ensure_signup_trial(target uuid) returns void
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare
  u record;
  h text;
  days integer := coalesce((public.moadoom_setting('trial_days'))::text::integer, 7);
  ends timestamptz;
begin
  select id, email, email_confirmed_at into u from auth.users where id = target;
  if u.id is null or u.email_confirmed_at is null or u.email is null then
    return;  -- 이메일 확인 전에는 체험 없음
  end if;
  h := encode(sha256(convert_to(lower(btrim(u.email)), 'UTF8')), 'hex');
  -- 같은 이메일 동시 실행 직렬화(트리거와 첫 로그인이 겹칠 때 중복 지급 방지)
  perform pg_advisory_xact_lock(hashtextextended('moadoom_trial:' || h, 0));
  if exists (select 1 from public.moadoom_trial_grants where user_id = u.id or email_hash = h) then
    return;  -- 이미 받은 계정/이메일
  end if;
  ends := u.email_confirmed_at + make_interval(days => days);
  insert into public.moadoom_trial_grants (user_id, email_hash, starts_at, ends_at)
  values (u.id, h, u.email_confirmed_at, ends);
  if exists (select 1 from public.moadoom_subscriptions where user_id = u.id) then
    return;  -- 소유자가 먼저 다른 이용권을 지정한 회원: 체험은 기록만 하고 덮어쓰지 않는다
  end if;
  insert into public.moadoom_subscriptions (user_id, kind, plan, starts_at, ends_at)
  values (u.id, 'trial', 'TRIAL', u.email_confirmed_at, ends);
  insert into public.moadoom_subscription_audit (actor_user_id, target_user_id, action, before_state, after_state, reason)
  values (null, u.id, 'trial_auto', jsonb_build_object('status', 'none'), public.moadoom_subscription_state(u.id),
          format('가입 이메일 확인 완료(%s) — 무료체험 %s일 자동 시작', public.moadoom_kst(u.email_confirmed_at), days));
  perform public.moadoom_enqueue_email(u.id, 'welcome_trial', 'welcome_trial:' || u.id::text,
    jsonb_build_object('starts_at', u.email_confirmed_at, 'ends_at', ends, 'days', days));
end $$;
revoke all on function public.moadoom_ensure_signup_trial(uuid) from public, anon, authenticated;

create or replace function public.moadoom_on_auth_user_confirmed() returns trigger
language plpgsql security definer set search_path = public, pg_temp as $$
begin
  if new.email_confirmed_at is not null and (tg_op = 'INSERT' or old.email_confirmed_at is null) then
    begin
      perform public.moadoom_ensure_signup_trial(new.id);
    exception when others then
      -- 체험 지급 실패가 이메일 확인 자체를 막으면 안 된다. 첫 이용권 조회 때 다시 시도된다.
      raise warning 'moadoom trial grant deferred for %: %', new.id, sqlerrm;
    end;
  end if;
  return new;
end $$;
revoke all on function public.moadoom_on_auth_user_confirmed() from public, anon, authenticated;

drop trigger if exists moadoom_auth_user_confirmed on auth.users;
create trigger moadoom_auth_user_confirmed
  after insert or update of email_confirmed_at on auth.users
  for each row execute function public.moadoom_on_auth_user_confirmed();

-- ---------------------------------------------------------------- 회원용 RPC
create or replace function public.moadoom_my_entitlement() returns jsonb
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare
  me uuid := auth.uid();
  role_name text;
  sub jsonb;
begin
  if me is null then
    raise exception 'authentication required' using errcode = '28000';
  end if;
  perform public.moadoom_ensure_signup_trial(me);  -- 트리거가 놓친 경우 자가 복구(1회 보장은 그대로)
  role_name := public.moadoom_role_of(me);
  sub := public.moadoom_subscription_state(me);
  return jsonb_build_object(
    'user_id', me,
    'role', role_name,
    'subscription', sub,
    -- 소유자는 이용권 상태와 무관하게 사용 가능(운영 권한과 이용권 분리)
    'effective_status', case when role_name = 'owner' then 'active' else sub->>'status' end,
    'server_now', now());
end $$;
revoke all on function public.moadoom_my_entitlement() from public, anon;
grant execute on function public.moadoom_my_entitlement() to authenticated;

-- ---------------------------------------------------------------- 관리용 RPC
create or replace function public.moadoom_require_role(allowed text[]) returns uuid
language plpgsql stable security definer set search_path = public, pg_temp as $$
declare
  me uuid := auth.uid();
begin
  if me is null then
    raise exception 'authentication required' using errcode = '28000';
  end if;
  if coalesce(public.moadoom_role_of(me), '') <> all (allowed) then
    raise exception 'operator role required' using errcode = '42501';
  end if;
  return me;
end $$;
revoke all on function public.moadoom_require_role(text[]) from public, anon, authenticated;

create or replace function public.moadoom_admin_search_members(q text default '')
returns table (user_id uuid, email text, created_at timestamptz, email_confirmed boolean,
               role text, subscription jsonb)
language plpgsql stable security definer set search_path = public, pg_temp as $$
declare
  pattern text;
begin
  perform public.moadoom_require_role(array['owner', 'admin']);
  pattern := '%' || replace(replace(replace(lower(coalesce(q, '')), '\', '\\'), '%', '\%'), '_', '\_') || '%';
  return query
    select u.id, u.email::text, u.created_at, (u.email_confirmed_at is not null),
           public.moadoom_role_of(u.id), public.moadoom_subscription_state(u.id)
    from auth.users u
    where lower(u.email) like pattern escape '\'
    order by u.email
    limit 50;
end $$;
revoke all on function public.moadoom_admin_search_members(text) from public, anon;
grant execute on function public.moadoom_admin_search_members(text) to authenticated;

create or replace function public.moadoom_admin_member_detail(target uuid) returns jsonb
language plpgsql stable security definer set search_path = public, pg_temp as $$
declare
  u record;
begin
  perform public.moadoom_require_role(array['owner', 'admin']);
  select id, email, created_at, email_confirmed_at into u from auth.users where id = target;
  if not found then
    raise exception 'member not found' using errcode = 'P0002';
  end if;
  return jsonb_build_object(
    'user_id', u.id, 'email', u.email, 'created_at', u.created_at,
    'email_confirmed', u.email_confirmed_at is not null, 'email_confirmed_at', u.email_confirmed_at,
    'role', public.moadoom_role_of(u.id),
    'subscription', public.moadoom_subscription_state(u.id),
    'audit', coalesce((
      select jsonb_agg(jsonb_build_object(
               'at', a.at, 'action', a.action, 'actor_email', coalesce(au.email, '시스템'),
               'before', a.before_state, 'after', a.after_state, 'reason', a.reason)
             order by a.at desc, a.id desc)
      from (select * from public.moadoom_subscription_audit
            where target_user_id = target order by at desc, id desc limit 100) a
      left join auth.users au on au.id = a.actor_user_id), '[]'::jsonb),
    'mail', coalesce((
      select jsonb_agg(jsonb_build_object(
               'kind', m.kind, 'status', m.status, 'attempts', m.attempts, 'created_at', m.created_at,
               'sent_at', m.sent_at, 'last_error', m.last_error)
             order by m.created_at desc, m.id desc)
      from (select * from public.moadoom_email_outbox
            where user_id = target order by created_at desc, id desc limit 50) m), '[]'::jsonb));
end $$;
revoke all on function public.moadoom_admin_member_detail(uuid) from public, anon;
grant execute on function public.moadoom_admin_member_detail(uuid) to authenticated;

-- action:
--   trial     무료체험 기간을 지금부터 days일(기본 trial_days)로 지정
--   term      기간지정: ends_on(한국 날짜) 그날 끝까지 사용
--   lifetime  무기한 무료(종료 시각 없음). 역할 부여 아님
--   adjust    종료 시각을 days일 만큼 연장(+) 또는 단축(-). lifetime에는 불가
--   suspend   이용정지 / resume 정지 해제(기간·종류 유지)
-- 현재 정책: 이용권 변경은 owner만. admin은 조회만.
create or replace function public.moadoom_admin_change_subscription(
  target uuid, action text, ends_on date default null, days integer default null, reason text default null)
returns jsonb
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare
  actor uuid;
  before_state jsonb;
  after_state jsonb;
  cur public.moadoom_subscriptions%rowtype;
  new_end timestamptz;
  audit_id bigint;
  trial_days integer := coalesce((public.moadoom_setting('trial_days'))::text::integer, 7);
begin
  actor := public.moadoom_require_role(array['owner']);
  if target = actor then
    raise exception 'cannot change own subscription' using errcode = '42501';
  end if;
  if reason is null or length(btrim(reason)) = 0 then
    raise exception 'reason required' using errcode = '22023';
  end if;
  if not exists (select 1 from auth.users where id = target) then
    raise exception 'member not found' using errcode = 'P0002';
  end if;
  select * into cur from public.moadoom_subscriptions where user_id = target for update;
  before_state := public.moadoom_subscription_state(target);

  if action = 'trial' then
    days := coalesce(days, trial_days);
    if days < 1 or days > 90 then
      raise exception 'trial days must be 1..90' using errcode = '22023';
    end if;
    insert into public.moadoom_subscriptions (user_id, kind, plan, starts_at, ends_at, suspended_at, updated_at)
    values (target, 'trial', 'TRIAL', now(), now() + make_interval(days => days), null, now())
    on conflict (user_id) do update set kind = 'trial', plan = 'TRIAL', starts_at = excluded.starts_at,
      ends_at = excluded.ends_at, suspended_at = null, updated_at = now();
  elsif action = 'term' then
    if ends_on is null then
      raise exception 'ends_on required' using errcode = '22023';
    end if;
    -- 한국 날짜 ends_on 하루가 끝나는 시각(다음날 00:00 KST)
    new_end := ((ends_on + 1)::timestamp - interval '9 hours') at time zone 'UTC';
    if new_end <= now() then
      raise exception 'ends_on must be today or later' using errcode = '22023';
    end if;
    insert into public.moadoom_subscriptions (user_id, kind, plan, starts_at, ends_at, suspended_at, updated_at)
    values (target, 'term', 'FREE', now(), new_end, null, now())
    on conflict (user_id) do update set kind = 'term', plan = 'FREE', starts_at = excluded.starts_at,
      ends_at = excluded.ends_at, suspended_at = null, updated_at = now();
  elsif action = 'lifetime' then
    insert into public.moadoom_subscriptions (user_id, kind, plan, starts_at, ends_at, suspended_at, updated_at)
    values (target, 'lifetime', 'FREE', now(), null, null, now())
    on conflict (user_id) do update set kind = 'lifetime', plan = 'FREE', starts_at = excluded.starts_at,
      ends_at = null, suspended_at = null, updated_at = now();
  elsif action = 'adjust' then
    if cur.user_id is null then
      raise exception 'no subscription to adjust' using errcode = '22023';
    end if;
    if cur.kind = 'lifetime' then
      raise exception 'lifetime has no end to adjust' using errcode = '22023';
    end if;
    if days is null or days = 0 or abs(days) > 3660 then
      raise exception 'days must be -3660..3660 and not 0' using errcode = '22023';
    end if;
    -- 연장: 이미 끝났으면 지금부터, 아니면 기존 종료 시각부터. 단축: 기존 종료 시각에서 뺀다.
    new_end := case when days > 0 then greatest(now(), cur.ends_at) else cur.ends_at end
               + make_interval(days => days);
    if new_end <= cur.starts_at then
      raise exception 'shortened end would be before start' using errcode = '22023';
    end if;
    update public.moadoom_subscriptions set ends_at = new_end, updated_at = now() where user_id = target;
  elsif action = 'suspend' then
    if cur.user_id is null then
      raise exception 'no subscription to suspend' using errcode = '22023';
    end if;
    if cur.suspended_at is not null then
      raise exception 'already suspended' using errcode = '22023';
    end if;
    update public.moadoom_subscriptions set suspended_at = now(), updated_at = now() where user_id = target;
  elsif action = 'resume' then
    if cur.user_id is null or cur.suspended_at is null then
      raise exception 'not suspended' using errcode = '22023';
    end if;
    update public.moadoom_subscriptions set suspended_at = null, updated_at = now() where user_id = target;
  else
    raise exception 'unknown action' using errcode = '22023';
  end if;

  after_state := public.moadoom_subscription_state(target);
  insert into public.moadoom_subscription_audit
    (actor_user_id, target_user_id, action, before_state, after_state, reason)
  values (actor, target, action, before_state, after_state, btrim(reason))
  returning id into audit_id;
  perform public.moadoom_enqueue_email(target, 'entitlement_changed', 'change:' || audit_id::text,
    jsonb_build_object('action', action, 'state', after_state));
  return after_state;
end $$;
revoke all on function public.moadoom_admin_change_subscription(uuid, text, date, integer, text) from public, anon;
grant execute on function public.moadoom_admin_change_subscription(uuid, text, date, integer, text) to authenticated;

create or replace function public.moadoom_admin_settings() returns jsonb
language plpgsql stable security definer set search_path = public, pg_temp as $$
begin
  perform public.moadoom_require_role(array['owner', 'admin']);
  return (select jsonb_object_agg(key, value) from public.moadoom_settings);
end $$;
revoke all on function public.moadoom_admin_settings() from public, anon;
grant execute on function public.moadoom_admin_settings() to authenticated;

-- 만료 전 안내 시점 설정(owner만). 1~30일 사이 정수 최대 5개.
create or replace function public.moadoom_admin_set_expiry_notice_days(days_list integer[]) returns jsonb
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare
  actor uuid;
  cleaned integer[];
begin
  actor := public.moadoom_require_role(array['owner']);
  select coalesce(array_agg(distinct d order by d desc), '{}') into cleaned from unnest(days_list) d;
  if cardinality(cleaned) > 5 or exists (select 1 from unnest(cleaned) d where d < 1 or d > 30) then
    raise exception 'notice days must be 1..30, at most 5' using errcode = '22023';
  end if;
  update public.moadoom_settings set value = to_jsonb(cleaned), updated_at = now(), updated_by = actor
  where key = 'expiry_notice_days';
  return to_jsonb(cleaned);
end $$;
revoke all on function public.moadoom_admin_set_expiry_notice_days(integer[]) from public, anon;
grant execute on function public.moadoom_admin_set_expiry_notice_days(integer[]) to authenticated;

-- ---------------------------------------------------------------- 메일 발송함(서비스 권한 전용)
-- 종료 전 안내: 설정된 일수 중 지금 해당하는 가장 가까운 시점 1건만 넣는다(종료 시각별·일수별 중복 방지).
create or replace function public.moadoom_enqueue_expiry_notices() returns integer
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare
  r record;
  d integer;
  n integer := 0;
  notice_days integer[];
begin
  select coalesce(array_agg(x::integer), '{}') into notice_days
  from jsonb_array_elements_text(coalesce(public.moadoom_setting('expiry_notice_days'), '[]'::jsonb)) x;
  for r in
    select s.user_id, s.kind, s.ends_at from public.moadoom_subscriptions s
    where s.kind in ('trial', 'term') and s.suspended_at is null and s.ends_at > now()
      and coalesce(public.moadoom_role_of(s.user_id), '') <> 'owner'  -- 소유자는 이용권과 무관하게 사용
  loop
    select min(x) into d from unnest(notice_days) x where now() >= r.ends_at - make_interval(days => x);
    if d is not null then
      insert into public.moadoom_email_outbox (user_id, to_email, kind, dedupe_key, payload)
      select r.user_id, u.email, 'expiry_notice',
             format('expiry:%s:%s:%s', r.user_id, floor(extract(epoch from r.ends_at))::bigint, d),
             jsonb_build_object('kind', r.kind, 'ends_at', r.ends_at, 'days_before', d)
      from auth.users u where u.id = r.user_id
      on conflict (dedupe_key) do nothing;
      if found then n := n + 1; end if;
    end if;
  end loop;
  return n;
end $$;
revoke all on function public.moadoom_enqueue_expiry_notices() from public, anon, authenticated;

-- 발송 대상 가져오기: queued 또는 10분 넘게 멈춘 sending(작업 중단 복구). 동시 실행 안전(skip locked).
create or replace function public.moadoom_mail_claim(batch integer default 20)
returns setof public.moadoom_email_outbox
language plpgsql volatile security definer set search_path = public, pg_temp as $$
begin
  return query
    update public.moadoom_email_outbox o
       set status = 'sending', attempts = o.attempts + 1, next_attempt_at = now() + interval '10 minutes'
     where o.id in (select id from public.moadoom_email_outbox
                     where status in ('queued', 'sending') and next_attempt_at <= now()
                     order by id limit greatest(1, least(batch, 100))
                     for update skip locked)
    returning o.*;
end $$;
revoke all on function public.moadoom_mail_claim(integer) from public, anon, authenticated;

-- 결과 기록: 성공은 공급자 응답(메시지 ID)이 있을 때만. 실패는 지수 백오프 재시도, 한도 초과 시 failed.
create or replace function public.moadoom_mail_result(mail_id bigint, ok boolean, error text default null,
                                                      provider_id text default null)
returns text
language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare
  m public.moadoom_email_outbox%rowtype;
begin
  select * into m from public.moadoom_email_outbox where id = mail_id for update;
  if m.id is null or m.status <> 'sending' then
    raise exception 'mail not in sending state' using errcode = '22023';
  end if;
  if ok and coalesce(btrim(provider_id), '') <> '' then
    update public.moadoom_email_outbox set status = 'sent', sent_at = now(), provider_message_id = provider_id,
      last_error = null where id = mail_id;
    return 'sent';
  end if;
  if m.attempts >= m.max_attempts then
    update public.moadoom_email_outbox set status = 'failed', last_error = left(coalesce(error, 'no provider id'), 500)
    where id = mail_id;
    return 'failed';
  end if;
  update public.moadoom_email_outbox
     set status = 'queued', last_error = left(coalesce(error, 'no provider id'), 500),
         next_attempt_at = now() + make_interval(mins => (power(2, m.attempts))::integer)
   where id = mail_id;
  return 'queued';
end $$;
revoke all on function public.moadoom_mail_result(bigint, boolean, text, text) from public, anon, authenticated;

do $$ begin
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function public.moadoom_enqueue_expiry_notices() to service_role;
    grant execute on function public.moadoom_mail_claim(integer) to service_role;
    grant execute on function public.moadoom_mail_result(bigint, boolean, text, text) to service_role;
  end if;
end $$;

commit;
