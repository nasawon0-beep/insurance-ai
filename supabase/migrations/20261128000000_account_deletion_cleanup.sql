begin;
-- Runs only when an account is actually deleted by the authenticated admin flow.
-- Installing this trigger does not delete any existing account or customer data.
create or replace function public.moadoom_cleanup_deleted_account()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  -- These review rows have restrictive FKs; remove them before installations/users.
  delete from public.moadoom_maintenance_reviews where user_id=old.id or reviewed_by=old.id;
  delete from public.moadoom_workspace_nonces where user_id=old.id;
  delete from public.moadoom_workspace_installations where user_id=old.id;
  delete from public.moadoom_email_outbox where user_id=old.id;
  delete from public.moadoom_trial_grants where user_id=old.id;
  delete from public.moadoom_subscription_audit where target_user_id=old.id or actor_user_id=old.id;
  delete from public.moadoom_subscriptions where user_id=old.id;
  delete from public.moadoom_member_roles where user_id=old.id;
  update public.moadoom_settings set updated_by=null where updated_by=old.id;
  return old;
end;
$$;
revoke all on function public.moadoom_cleanup_deleted_account() from public,anon,authenticated;
drop trigger if exists moadoom_account_cleanup_before_delete on auth.users;
create trigger moadoom_account_cleanup_before_delete before delete on auth.users
for each row execute function public.moadoom_cleanup_deleted_account();
comment on function public.moadoom_cleanup_deleted_account() is '2026-11-28: owner-approved account deletion cleanup. No retention of trial hash after account deletion; no local customer files touched.';
commit;
