"""LangGraph role orchestration, shared tool gateway and independent validation."""
import json
import logging
import os
import hashlib
import time
from datetime import datetime, timezone
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from .analysis import compare, compatible, metrics, quality_errors
from .catalog import Catalog
from .models import AnalysisRequest, AnalysisResult, DemoPolicy, Event
from .skills import SKILLS

LOG = logging.getLogger("cooling_advisor")


class GuardError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Runtime:
    def __init__(self, request, catalog, policy, case_id, allowed_zones):
        self.request = request
        self.catalog = catalog
        self.policy = policy
        self.allowed_zones = frozenset(allowed_zones)
        self.started = time.monotonic()
        self.result = AnalysisResult(case_id=case_id, scenario_id=request.scenario_id,
                                     mode=request.mode, status="failed", policy_version=policy.version)
        self.proposed_id = None

    def event(self, actor, action, status="passed", skill=None, detail=""):
        event = Event(timestamp=datetime.now(timezone.utc).isoformat(), case_id=self.result.case_id,
                      step=len(self.result.events) + 1, actor=actor, action=action,
                      status=status, skill_id=skill, detail=detail, elapsed_ms=round((time.monotonic()-self.started)*1000, 2))
        self.result.events.append(event)
        LOG.info(json.dumps(event.model_dump()))

    def check_deadline(self):
        if time.monotonic() - self.started >= self.policy.deadline_seconds:
            raise GuardError("DEADLINE_EXCEEDED")

    def activate(self, skill_id, actor):
        self.check_deadline()
        skill = SKILLS[skill_id]
        if skill.owner != actor:
            raise GuardError("SKILL_OWNER_MISMATCH")
        instructions = skill.instructions()
        self.event(actor, "skill_loaded", skill=skill_id, detail=f"{skill.name} · {skill.version}")
        return instructions

    def scoped_scenario(self):
        scenario = self.catalog.get(self.request.scenario_id)
        if scenario.zone_id not in self.allowed_zones:
            raise GuardError("ACCESS_DENIED")
        return scenario

    def baseline_run(self):
        scenario = self.scoped_scenario()
        runs = [r for r in scenario.experiments if r.experiment_id == scenario.baseline_id]
        if len(runs) != 1:
            raise GuardError("INVALID_BASELINE_REFERENCE")
        baseline = runs[0]
        if (baseline.zone_id != scenario.zone_id or baseline.scenario_id != scenario.scenario_id
                or baseline.snapshot_id != scenario.snapshot_id):
            raise GuardError("BASELINE_SCOPE_MISMATCH")
        return baseline

    def call(self, tool, actor, skill_id, function):
        self.check_deadline()
        skill = SKILLS[skill_id]
        if skill.owner != actor or tool not in skill.tools:
            self.event("G2", tool, "rejected", skill_id, "TOOL_NOT_ALLOWED")
            raise GuardError("TOOL_NOT_ALLOWED")
        self.scoped_scenario()
        if self.result.tool_calls >= self.policy.tool_budget:
            self.event("G2", tool, "rejected", skill_id, "TOOL_BUDGET_EXCEEDED")
            raise GuardError("TOOL_BUDGET_EXCEEDED")
        self.result.tool_calls += 1  # Reserve before executing, including nested dispatch calls.
        self.event("G2", tool, "authorized", skill_id)
        try:
            value = function()
            self.check_deadline()
        except Exception:
            self.event(actor, tool, "failed", skill_id, "Tool did not complete")
            raise
        self.event(actor, tool, "completed", skill_id)
        return value


class GraphState(TypedDict, total=False):
    runtime: Runtime
    ready: bool


def supervisor_plan(state):
    rt = state["runtime"]
    rt.activate("S1", "supervisor")
    rt.scoped_scenario()
    rt.event("supervisor", "plan", detail=f"Data Analyst first; mode={rt.request.mode}")
    return {}


def data_analyst(state):
    rt = state["runtime"]

    def work():
        rt.activate("S2", "data_analyst")
        baseline = rt.call("read_telemetry", "data_analyst", "S2", rt.baseline_run)
        errors = rt.call("inspect_data_quality", "data_analyst", "S2", lambda: quality_errors(baseline))
        if errors:
            rt.result.status = "insufficient_data"
            rt.result.reason_codes = errors
            return False
        rt.activate("S3", "data_analyst")
        rt.result.baseline = rt.call("compute_baseline", "data_analyst", "S3", lambda: metrics(baseline, rt.policy))
        return True

    ready = rt.call("call_data_analyst", "supervisor", "S1", work)
    rt.event("supervisor", "receive_data_analysis", "completed" if ready else "blocked")
    return {"ready": ready}


def cooling_advisor(state):
    rt = state["runtime"]

    def work():
        rt.activate("S4", "cooling_advisor")
        baseline = rt.baseline_run()
        alternatives = rt.call("lookup_experiments", "cooling_advisor", "S4", lambda: [
            run for run in rt.scoped_scenario().experiments
            if run.experiment_id != baseline.experiment_id and compatible(run, baseline)
        ])
        if not rt.scoped_scenario().comparison_enabled or not alternatives:
            rt.result.status = "not_supported"
            rt.result.reason_codes = ["NO_MATCHING_ALTERNATIVES"]
            return
        rt.result.candidates = rt.call("compare_candidates", "cooling_advisor", "S4",
                                      lambda: compare([baseline, *alternatives], baseline, rt.policy))
        feasible = [row for row in rt.result.candidates if row.feasible]
        rt.proposed_id = min(feasible, key=lambda row: (row.total_kwh, row.experiment_id)).experiment_id if feasible else None

    rt.call("call_cooling_advisor", "supervisor", "S1", work)
    rt.event("supervisor", "receive_candidate_results", "completed")
    return {}


def validate(state):
    rt = state["runtime"]
    rt.check_deadline()
    if not state.get("ready"):
        rt.event("G3", "validate_evidence", "blocked", detail="Required temperature/time evidence is missing")
        return {}
    baseline = rt.baseline_run()  # Re-read authoritative evidence, not the model's metrics.
    rt.result.baseline = metrics(baseline, rt.policy)
    if quality_errors(baseline):
        raise GuardError("EVIDENCE_CHANGED")
    if rt.request.mode == "analyze":
        rt.result.status = "completed"
        rt.event("G3", "validate_analysis", detail="Analysis-only result; no setting recommendation")
        return {}
    alternatives = [r for r in rt.scoped_scenario().experiments
                    if r.experiment_id != baseline.experiment_id and compatible(r, baseline)]
    if not rt.scoped_scenario().comparison_enabled or not alternatives:
        rt.result.status = "not_supported"
        rt.result.reason_codes = ["NO_MATCHING_ALTERNATIVES"]
        rt.event("G3", "validate_comparability", "blocked", detail="No exact compatible experiment")
        return {}
    trusted = compare([baseline, *alternatives], baseline, rt.policy)
    rt.result.candidates = trusted
    feasible = [row for row in trusted if row.feasible]
    for row in trusted:
        rt.event("G3", "candidate_check", "passed" if row.feasible else "rejected",
                 detail=f"{row.experiment_id}: {', '.join(row.reasons) or 'within configured policy'}")
    if not feasible:
        rt.result.status = "no_feasible_option"
        rt.result.reason_codes = ["NO_FEASIBLE_CANDIDATE"]
        return {}
    selected = min(feasible, key=lambda row: (row.total_kwh, row.experiment_id))
    if rt.proposed_id != selected.experiment_id:
        raise GuardError("PROPOSAL_EVIDENCE_MISMATCH")
    rt.result.status = "completed"
    rt.result.selected = selected
    rt.result.decision = "no_change" if selected.experiment_id == baseline.experiment_id else "recommend_change"
    rt.event("G3", "validate_recommendation", detail=f"Approved evidence: {selected.experiment_id}")
    return {}


def supervisor_finalize(state):
    rt = state["runtime"]
    rt.activate("S5", "supervisor")
    result = rt.result
    if result.status == "completed" and result.selected:
        item = result.selected
        saving = (f"{abs(item.savings_pct):.1f}% {'below' if item.savings_pct >= 0 else 'above'} baseline"
                  if item.savings_pct is not None else "undefined percentage versus baseline")
        result.explanation = (f"{'Keep the existing setting' if result.decision == 'no_change' else 'Review the proposed setting'}: "
                              f"{item.setpoint_c:g} °C. {result.source_kind.capitalize()} total energy is {item.total_kwh:.2f} kWh "
                              f"({saving}), with maximum inlet {item.max_inlet_c:.1f} °C. "
                              "This compares the supplied operating-point evidence. Review model limits before interpreting savings; no equipment command was issued.")
    elif result.status == "completed":
        result.explanation = (f"Baseline {result.source_kind} total energy is {result.baseline.total_kwh:.2f} kWh. "
                              "Analysis-only mode did not select an alternative setting.")
    else:
        result.explanation = "No recommendation was released. " + ", ".join(result.reason_codes)
    rt.event("supervisor", "finalize", "completed", "S5")
    if rt.request.use_gemini and result.status == "completed":
        from .llm import explain
        try:
            rt.check_deadline()
            if result.model_calls >= rt.policy.model_call_budget:
                raise GuardError("MODEL_BUDGET_EXCEEDED")
            result.model_calls += 1
            remaining = rt.policy.deadline_seconds - (time.monotonic() - rt.started)
            result.gemini_explanation = explain(result, SKILLS["S5"].instructions(), min(15, remaining))
            rt.check_deadline()
            result.workflow_mode += "_with_gemini_explanation"
            rt.event("supervisor", "gemini_explanation", "completed", "S5")
        except GuardError:
            raise
        except Exception as exc:
            rt.event("supervisor", "gemini_explanation", "failed", "S5", type(exc).__name__ + "; deterministic explanation retained")
    return {}


def build_graph():
    builder = StateGraph(GraphState)
    builder.add_node("supervisor_plan", supervisor_plan)
    builder.add_node("data_analyst", data_analyst)
    builder.add_node("cooling_advisor", cooling_advisor)
    builder.add_node("guardrail_g3", validate)
    builder.add_node("supervisor_finalize", supervisor_finalize)
    def agentic_node(state):
        from .agentic import run_agents
        return run_agents(state['runtime'])
    builder.add_node('gemini_agents',agentic_node)
    builder.add_conditional_edges(START,lambda s: 'gemini_agents' if s['runtime'].request.agent_mode == 'gemini' else 'supervisor_plan')
    builder.add_edge('gemini_agents','guardrail_g3')
    builder.add_edge("supervisor_plan", "data_analyst")
    builder.add_conditional_edges("data_analyst", lambda s: "cooling_advisor" if s.get("ready") and s["runtime"].request.mode == "recommend" else "guardrail_g3")
    builder.add_edge("cooling_advisor", "guardrail_g3")
    builder.add_edge("guardrail_g3", "supervisor_finalize")
    builder.add_edge("supervisor_finalize", END)
    return builder.compile()


GRAPH = build_graph()


def run_analysis(request: AnalysisRequest, *, catalog=None, policy=None, case_id=None,
                 allowed_zones=("zone-a",)) -> AnalysisResult:
    catalog = catalog or Catalog()
    try:
        scenario = catalog.get(request.scenario_id)
    except ValueError:
        scenario = None
    rt = Runtime(request, catalog, policy or (scenario.policy if scenario else None) or DemoPolicy(), case_id or str(uuid4()), allowed_zones)
    try:
        rt.scoped_scenario()
        rt.result.source_kind = rt.baseline_run().source_kind
        rt.result.provenance = scenario.provenance
        rt.result.execution = {'request':request.model_dump(), 'policy':rt.policy.model_dump(),
            'model':os.getenv('GEMINI_MODEL','gemini-flash-latest') if request.agent_mode=='gemini' or request.use_gemini else None,
            'skill_manifest':{sid:{'version':skill.version,'sha256':hashlib.sha256(skill.instructions().encode()).hexdigest()}
                              for sid,skill in SKILLS.items()}}
        rt.result.limitations = scenario.provenance.get('limitations', ['Hand-authored synthetic fixtures; not measured operational savings'])
        rt.event("G1", "validate_request", detail="Authenticated catalog and zone scope validated")
        GRAPH.invoke({"runtime": rt}, config={"recursion_limit": 12})
    except GuardError as exc:
        rt.result.status = "budget_exceeded" if exc.code in ("DEADLINE_EXCEEDED", "TOOL_BUDGET_EXCEEDED", "MODEL_BUDGET_EXCEEDED") else "failed"
        rt.result.selected = None
        rt.result.decision = "unavailable"
        rt.result.reason_codes = [exc.code]
        rt.result.explanation = "No recommendation was released: " + exc.code
        rt.event("workflow", "stop", "blocked", detail=exc.code)
    except Exception as exc:
        rt.result.status = "failed"
        rt.result.selected = None
        rt.result.decision = "unavailable"
        rt.result.reason_codes = ["GEMINI_NOT_CONFIGURED" if str(exc) == "GEMINI_NOT_CONFIGURED" else "WORKFLOW_ERROR"]
        rt.result.explanation = "The workflow failed; no recommendation was released."
        rt.event("workflow", "stop", "failed", detail=type(exc).__name__)
    rt.result.elapsed_ms = round((time.monotonic()-rt.started)*1000, 2)
    return AnalysisResult.model_validate(rt.result.model_dump())
