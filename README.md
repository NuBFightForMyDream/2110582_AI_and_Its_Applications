# Cooling Advisor

A Python workspace for repeatable data-center cooling studies, baseline analysis, bounded parent/child agents and independently checked recommendations. Project documentation and UI are in English.

## Run locally

On this machine the environment and pinned SustainDC source are already installed:

```bash
.venv/bin/python -m cooling_advisor
```

Open [the dashboard](http://127.0.0.1:8501) or [API documentation](http://127.0.0.1:8000/docs). The launcher starts Streamlit and FastAPI together on loopback, creates a private random API token in ignored `.env` when needed, and stops both child processes when terminated. Override occupied ports with `--api-port 8001 --port 8502`.

For a fresh Python 3.12 environment:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip install -e '.[dev,integrations]' --no-deps
.venv/bin/python -m cooling_advisor.simulation install
.venv/bin/python -m cooling_advisor
```

Docker is not required. The simulator installer downloads the official repository and verifies one pinned revision. Without that checkout, included precomputed studies and offline analysis still work; creating new experiments requires installation.

## What to try

The dashboard is a single page:

1. **Pick a situation** (a bundled SustainDC study or one you simulated).
2. **Read the answer:** the recommended setpoint, the whole-data-center kWh saved versus the current setting, and the hottest rack inlet against the limit.
3. **Check the chart:** total electricity per setpoint, split into IT equipment and cooling system. "All setpoints" lists the numbers.
4. **Simulate a new situation** (location, day of year, hours) when the pinned simulator is installed.

CSV imports, Gemini agent mode, case history, review records and monitoring remain available through the API (`/docs`) but are not shown in the dashboard. Four synthetic fixtures remain available to the API and tests: Steady load (reject the hotter lower-energy option), Peak load (keep baseline), Missing temperature (insufficient_data), and Unmatched experiment (not_supported).

## Evidence and limitations

The included **Phoenix summer** and **New York winter** studies were generated from the pinned [SustainDC source](https://github.com/HewlettPackard/dc-rl/tree/a92b4755aca560e34a98d14028dda629eb968482). The adapter executes the upstream power-temperature submodel, **not the full dynamic RL environment**. Hourly public workload and TMY weather are paired by index and held over 15-minute intervals. The model has no thermal inertia. It is not calibrated to a real facility.

The electrical boundary includes CPU, IT fans, CRAC fan, tower fan, compressor and two pumps. CRAC thermal load is excluded from electrical energy. Source hashes, commit, configuration, seed, sampling, adapter adjustments and limitations travel with each scenario and result. The adapter fixes nondeterministic rack ordering without changing upstream files.

Lower setpoints dominate in the included default-model studies. The system reports this directly; it does not establish that AI beats a tuned baseline or saves real facility energy. A maximum-inlet threshold here is a study setting, not engineering approval. User-imported CSV is labeled source-unverified. No tool controls physical equipment.

Regenerate included studies:

```bash
.venv/bin/python offline/generate_sustaindc_studies.py
```

## Agent execution modes

- **Offline:** actual LangGraph, fixed Python role procedures, five loaded skills, guarded tools and independent G3 verification. No LLM calls; free text is retained as context but does not change task interpretation.
- **Gemini:** actual manual function calling for a Supervisor and two specialist loops with separate histories. The model chooses only tools currently allowed by the phase. Shared model/tool budgets and a deadline apply across nested children. G3 rereads authoritative evidence, recomputes all options and rejects unsupported proposals. Final numerical text is composed by Python; model notes are labeled separately.

Set `GEMINI_API_KEY` and `GEMINI_MODEL` in backend `.env`, restart, select Gemini mode and explicitly allow transmission of the case summary/tool results. No model call occurs by default. Model availability, billing and quotas depend on your account. The configured alias defaults to `gemini-flash-latest`; resolved model versions are recorded when returned by the provider. Prefer an explicit supported model version for reproducible evaluation.

**Live Gemini is not verified in this workspace because no API key is configured.** Scripted-provider tests exercise the real parent/child orchestration and guardrails without an external paid model. A provider/configuration failure never silently becomes a successful Gemini run.

## Storage and authentication

Default: private local SQLite cases/reviews plus immutable local scenario JSON and hash-addressed CSV artifacts under ignored `.local/`. API access requires the generated local bearer token. This is a single local operator configuration, not user login.

Optional Supabase:

1. Apply `supabase/migrations/002_user_workspace.sql` in your project. Migration 001 is legacy and is not required for the new workspace.
2. Create a user through your Supabase administration flow and grant `zone-a` membership using the commented onboarding SQL.
3. Configure `COOLING_AUTH_MODE=supabase`, `COOLING_STORAGE=supabase`, `SUPABASE_URL` and `SUPABASE_ANON_KEY` in backend secrets; restart.
4. Sign in through the dashboard. FastAPI verifies the JWT with Supabase Auth and loads approved memberships. Case and review queries use the user JWT and RLS, not a service-role key.

**Live Supabase is not verified because project credentials are absent.** Scenario files and raw artifacts still use the backend's persistent disk, even in Supabase case-storage mode. Shared object storage, retention administration and multi-instance catalog synchronization are future work. User-owned database records are not cryptographically tamper-evident audit logs.

## API and verification

All business routes require bearer authentication; `/health` is public and does not expose secrets. Main routes: `/settings`, `/catalog`, `/scenarios/{id}`, `/simulations`, `/imports`, `/analysis`, `/cases`, `/cases/{id}/events`, `/cases/{id}/decision`, `/monitoring`. Analysis requires `Idempotency-Key`. Read-only API docs are available locally; Swagger requests require the token. No execute/actuator endpoint exists.

```bash
.venv/bin/python -m pytest -q
```

Tests cover actual isolated upstream simulation/repeatability, component-energy conservation, baseline ranking, temperature violations, missing/incompatible evidence, CSV validation, owner isolation, API auth/idempotency/rate and body limits, scripted LLM delegation, forged proposals and all dashboard pages. Simulator integration tests skip explicitly if upstream source is unavailable. Provider SDK request formatting is tested with a local HTTP mock, not a live provider account.

This release runs locally. No cloud account, public deployment, BMS connection or external uptime/alerting service has been configured. Start with [the component architecture index](architecture.md), [implementation status](architecture/prototype-status.md) or [local runbook](architecture/deployment/local-runbook.md).
