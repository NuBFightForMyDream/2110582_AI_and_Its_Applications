import pytest
from pydantic import ValidationError

from cooling_advisor.analysis import metrics
from cooling_advisor.catalog import Catalog
from cooling_advisor.engine import GuardError, Runtime, run_analysis, validate
from cooling_advisor.models import AnalysisRequest, DemoPolicy
from cooling_advisor.storage import ConflictError, SQLiteStore, execute_case


def test_supported_saving_excludes_hotter_lower_energy_option():
    result = run_analysis(AnalysisRequest(scenario_id="steady"))
    assert result.status == "completed"
    assert result.selected.experiment_id == "steady-19"
    assert result.selected.total_kwh == pytest.approx(137)
    assert result.baseline.total_kwh == pytest.approx(142)
    assert result.selected.savings_pct == pytest.approx(100 * 5 / 142)
    hot = next(row for row in result.candidates if row.experiment_id == "steady-20")
    assert hot.total_kwh < result.selected.total_kwh
    assert not hot.feasible
    assert "MAX_INLET_LIMIT_EXCEEDED" in hot.reasons
    assert {e.skill_id for e in result.events if e.skill_id} == {"S1", "S2", "S3", "S4", "S5"}
    assert result.source_kind == "synthetic"


@pytest.mark.parametrize("scenario,status,decision", [
    ("peak", "completed", "no_change"),
    ("missing", "insufficient_data", "unavailable"),
    ("unsupported", "not_supported", "unavailable"),
])
def test_domain_outcomes(scenario, status, decision):
    result = run_analysis(AnalysisRequest(scenario_id=scenario))
    assert result.status == status
    assert result.decision == decision
    if decision == "unavailable":
        assert result.selected is None


def test_analysis_only_skips_cooling_advisor():
    result = run_analysis(AnalysisRequest(scenario_id="steady", mode="analyze"))
    assert result.status == "completed"
    assert result.selected is None
    assert not any(e.skill_id == "S4" for e in result.events)


def test_no_feasible_alternative():
    result = run_analysis(AnalysisRequest(scenario_id="steady"), policy=DemoPolicy(max_inlet_c=23))
    assert result.status == "no_feasible_option"
    assert result.selected is None


def test_shared_budget_stops_nested_tools():
    result = run_analysis(AnalysisRequest(scenario_id="steady"), policy=DemoPolicy(tool_budget=1))
    assert result.status == "budget_exceeded"
    assert result.tool_calls == 1
    assert result.selected is None


def test_unauthorized_zone_and_tool_do_not_execute():
    req = AnalysisRequest(scenario_id="steady")
    blocked = run_analysis(req, allowed_zones=("another-zone",))
    assert blocked.reason_codes == ["ACCESS_DENIED"]
    rt = Runtime(req, Catalog(), DemoPolicy(), "test", ("zone-a",))
    with pytest.raises(GuardError, match="TOOL_NOT_ALLOWED"):
        rt.call("delete_data", "data_analyst", "S2", lambda: pytest.fail("must not execute"))


def test_g3_rejects_forged_proposal():
    rt = Runtime(AnalysisRequest(scenario_id="steady"), Catalog(), DemoPolicy(), "test", ("zone-a",))
    rt.proposed_id = "steady-20"
    with pytest.raises(GuardError, match="PROPOSAL_EVIDENCE_MISMATCH"):
        validate({"runtime": rt, "ready": True})


def test_incomplete_intervals_and_nonfinite_values():
    catalog = Catalog()
    scenario = catalog.get("steady")
    scenario.experiments[0].samples.pop()
    result = run_analysis(AnalysisRequest(scenario_id="steady"), catalog=Catalog([scenario]))
    assert result.status == "insufficient_data"
    assert "INCOMPLETE_HORIZON" in result.reason_codes
    with pytest.raises(ValidationError):
        DemoPolicy(max_inlet_c=float("nan"))
    with pytest.raises(ValidationError):
        AnalysisRequest(scenario_id="steady", max_inlet_c=100)


def test_energy_uses_hours_and_actual_power():
    run = Catalog().get("steady").experiments[0]
    result = metrics(run, DemoPolicy())
    assert result.cooling_kwh == 42
    assert result.it_kwh == 100


def test_case_persistence_and_idempotency(tmp_path):
    store = SQLiteStore(tmp_path / "cases.db")
    req = AnalysisRequest(scenario_id="steady")
    first = execute_case(req, "same-key", store)
    second = execute_case(req, "same-key", store)
    assert first.case_id == second.case_id
    assert len(store.recent()) == 1
    assert store.get(first.case_id).selected.experiment_id == "steady-19"
    store.decision(first.case_id, "mark_for_review")
    store.decision(first.case_id, "mark_for_review")
    with pytest.raises(ConflictError):
        execute_case(AnalysisRequest(scenario_id="peak"), "same-key", store)


def test_optional_model_failure_keeps_validated_result(monkeypatch):
    def unavailable(*args):
        raise TimeoutError("provider unavailable")
    monkeypatch.setattr("cooling_advisor.llm.explain", unavailable)
    result = run_analysis(AnalysisRequest(scenario_id="steady", use_gemini=True))
    assert result.status == "completed"
    assert result.selected.experiment_id == "steady-19"
    assert result.gemini_explanation is None
    assert any(e.action == "gemini_explanation" and e.status == "failed" for e in result.events)


def test_global_deadline_guard_is_not_swallowed_by_optional_explanation(monkeypatch):
    def expired(*args):
        raise GuardError("DEADLINE_EXCEEDED")
    monkeypatch.setattr("cooling_advisor.llm.explain", expired)
    result = run_analysis(AnalysisRequest(scenario_id="steady", use_gemini=True))
    assert result.status == "budget_exceeded"
    assert result.selected is None
    assert result.decision == "unavailable"
