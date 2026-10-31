-- Apply manually in your Supabase SQL editor. No service-role key is used by the app.
begin;
create table if not exists public.cooling_memberships (
  user_id uuid not null references auth.users(id) on delete cascade,
  zone_id text not null,
  primary key(user_id, zone_id)
);
create table if not exists public.cooling_cases (
  id uuid primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  dedupe_key text not null,
  request_hash text not null,
  created_at timestamptz not null default now(),
  result jsonb,
  unique(owner_id,dedupe_key)
);
create table if not exists public.cooling_decisions (
  case_id uuid not null references public.cooling_cases(id) on delete cascade,
  owner_id uuid not null references auth.users(id) on delete cascade,
  action text not null check(action in ('acknowledge','mark_for_review','dismiss')),
  created_at timestamptz not null default now(),
  primary key(case_id,action)
);
alter table public.cooling_memberships enable row level security;
alter table public.cooling_cases enable row level security;
alter table public.cooling_decisions enable row level security;
drop policy if exists own_memberships on public.cooling_memberships;
create policy own_memberships on public.cooling_memberships for select to authenticated using(user_id=auth.uid());
drop policy if exists own_cases on public.cooling_cases;
create policy own_cases on public.cooling_cases for select to authenticated using(owner_id=auth.uid());
drop policy if exists insert_own_case on public.cooling_cases;
create policy insert_own_case on public.cooling_cases for insert to authenticated with check(
  owner_id=auth.uid() and exists(select 1 from public.cooling_memberships where user_id=auth.uid()));
drop policy if exists update_own_case on public.cooling_cases;
-- A case result is written once; finalized rows cannot be rewritten.
create policy update_own_case on public.cooling_cases for update to authenticated using(owner_id=auth.uid() and result is null) with check(owner_id=auth.uid());
drop policy if exists own_decisions on public.cooling_decisions;
create policy own_decisions on public.cooling_decisions for select to authenticated using(owner_id=auth.uid());
drop policy if exists insert_own_decision on public.cooling_decisions;
create policy insert_own_decision on public.cooling_decisions for insert to authenticated with check(
  owner_id=auth.uid() and exists(select 1 from public.cooling_cases where id=case_id and owner_id=auth.uid()));
-- Upsert identical review actions is idempotent; no delete permission is granted.
drop policy if exists update_own_decision on public.cooling_decisions;
create policy update_own_decision on public.cooling_decisions for update to authenticated using(owner_id=auth.uid()) with check(
  owner_id=auth.uid() and exists(select 1 from public.cooling_cases where id=case_id and owner_id=auth.uid()));
-- Supabase default privileges grant ALL on new public tables; replace them with column-scoped grants.
revoke all on public.cooling_memberships,public.cooling_cases,public.cooling_decisions from anon,authenticated;
grant select on public.cooling_memberships to authenticated;
-- Cases are reserved without a result, then only the result column is finalized.
grant select on public.cooling_cases to authenticated;
grant insert(id,owner_id,dedupe_key,request_hash) on public.cooling_cases to authenticated;
grant update(result) on public.cooling_cases to authenticated;
grant select,insert,update on public.cooling_decisions to authenticated;
commit;
-- Admin onboarding (replace UUID):
-- insert into public.cooling_memberships(user_id,zone_id) values ('USER_UUID','zone-a');
