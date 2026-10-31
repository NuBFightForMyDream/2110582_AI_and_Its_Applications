-- Optional single-operator backend storage. No public/anon access is granted.
-- Run manually in your own Supabase project. This is not the production RLS model.
create table if not exists public.cooling_demo_cases (
  id text primary key,
  owner_id text not null,
  dedupe_key text not null,
  request_hash text not null,
  created_at timestamptz not null default now(),
  result jsonb,
  unique (owner_id, dedupe_key)
);

create table if not exists public.cooling_demo_decisions (
  case_id text not null references public.cooling_demo_cases(id),
  action text not null check (action in ('acknowledge','mark_for_review','dismiss')),
  created_at timestamptz not null default now(),
  primary key (case_id, action)
);

alter table public.cooling_demo_cases enable row level security;
alter table public.cooling_demo_decisions enable row level security;
revoke all on public.cooling_demo_cases from anon, authenticated;
revoke all on public.cooling_demo_decisions from anon, authenticated;
grant select, insert, update on public.cooling_demo_cases to service_role;
grant select, insert, update on public.cooling_demo_decisions to service_role;
