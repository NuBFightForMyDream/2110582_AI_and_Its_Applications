from typing import Literal
import math

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceKind = Literal['synthetic', 'simulated', 'measured']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class AnalysisRequest(StrictModel):
    scenario_id: str = Field(min_length=1, max_length=80)
    mode: Literal['analyze', 'recommend'] = 'recommend'
    question: str = Field(default='Compare supported cooling options and explain the trade-offs.', max_length=2000)
    agent_mode: Literal['rules', 'gemini'] = 'rules'
    use_gemini: bool = False  # Backward-compatible optional explanation.


class Sample(StrictModel):
    step: int = Field(ge=0)
    interval_hours: float = Field(gt=0, le=24)
    cooling_kw: float = Field(ge=0)
    it_kw: float = Field(ge=0)
    max_inlet_c: float | None = None
    max_outlet_c: float | None = None
    ambient_c: float | None = None
    cpu_load_fraction: float | None = Field(default=None, ge=0, le=1)
    components_kw: dict[str, float] = Field(default_factory=dict)


class Policy(StrictModel):
    version: str = 'synthetic-demo-policy-v1'
    max_inlet_c: float = Field(default=27.0, ge=10, le=45)
    min_setpoint_c: float = Field(default=16.0, ge=10, le=35)
    max_setpoint_c: float = Field(default=24.0, ge=10, le=35)
    tool_budget: int = Field(default=20, ge=0, le=100)
    model_call_budget: int = Field(default=14, ge=0, le=30)
    deadline_seconds: float = Field(default=120, gt=0, le=300)

    @model_validator(mode='after')
    def valid_range(self):
        if self.min_setpoint_c > self.max_setpoint_c:
            raise ValueError('Setpoint bounds are reversed')
        return self


DemoPolicy = Policy  # Original prototype import compatibility.


class Experiment(StrictModel):
    experiment_id: str
    scenario_id: str
    snapshot_id: str
    zone_id: str
    horizon_hours: float = Field(gt=0, le=168)
    energy_boundary: str = Field(min_length=1, max_length=160)
    source_kind: SourceKind
    setpoint_c: float
    samples: list[Sample] = Field(min_length=1, max_length=672)
    comparison_fingerprint: str = ''


class Scenario(StrictModel):
    scenario_id: str
    label: str
    description: str
    zone_id: str
    snapshot_id: str
    baseline_id: str
    experiments: list[Experiment] = Field(min_length=1, max_length=12)
    owner_id: str | None = None
    comparison_enabled: bool = True
    policy: Policy | None = None
    provenance: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def valid_evidence(self):
        ids = [r.experiment_id for r in self.experiments]
        if len(ids) != len(set(ids)) or ids.count(self.baseline_id) != 1:
            raise ValueError('Unique experiment IDs and exactly one baseline are required')
        return self


class SimulationRequest(StrictModel):
    label: str = Field(default='SustainDC experiment', min_length=1, max_length=80)
    city: Literal['AZ', 'NY', 'WA'] = 'AZ'
    workload: Literal['alibaba1', 'alibaba2', 'google1'] = 'alibaba1'
    day_of_year: int = Field(default=182, ge=1, le=363)
    hours: int = Field(default=12, ge=1, le=48)
    capacity_mw: float = Field(default=1.0, ge=0.1, le=2.0)
    baseline_setpoint_c: float = Field(default=18, ge=15, le=21.6)
    candidate_setpoints: list[float] = Field(default_factory=lambda: [15, 16, 17, 18, 19, 20, 21.6], min_length=1, max_length=8)
    max_inlet_c: float = Field(default=27, ge=20, le=32)
    seed: int = Field(default=42, ge=0, le=2**31-1)

    @model_validator(mode='after')
    def valid_settings(self):
        if any(not math.isfinite(v) or not 15 <= v <= 21.6 for v in self.candidate_setpoints):
            raise ValueError('Candidate settings must be finite and within 15–21.6 °C')
        self.candidate_setpoints = sorted(set([self.baseline_setpoint_c, *self.candidate_setpoints]))
        if len(self.candidate_setpoints) > 8:
            raise ValueError('At most eight settings including the baseline')
        return self


class Metrics(StrictModel):
    experiment_id: str
    setpoint_c: float
    cooling_kwh: float
    it_kwh: float
    total_kwh: float
    max_inlet_c: float | None
    feasible: bool
    reasons: list[str] = Field(default_factory=list)
    savings_pct: float | None = None
    components_kwh: dict[str, float] = Field(default_factory=dict)


class Event(StrictModel):
    timestamp: str
    case_id: str
    step: int
    actor: str
    action: str
    status: str
    skill_id: str | None = None
    detail: str = ''
    elapsed_ms: float = 0
    model_tokens: int | None = None


class AnalysisResult(StrictModel):
    case_id: str
    scenario_id: str
    mode: str
    status: Literal['completed', 'clarification_required', 'insufficient_data', 'not_supported', 'no_feasible_option', 'budget_exceeded', 'failed']
    decision: Literal['recommend_change', 'no_change', 'unavailable'] = 'unavailable'
    source_kind: SourceKind = 'synthetic'
    workflow_mode: str = 'deterministic_langgraph'
    policy_version: str
    baseline: Metrics | None = None
    selected: Metrics | None = None
    candidates: list[Metrics] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    explanation: str = ''
    gemini_explanation: str | None = None
    agent_notes: dict[str, str] = Field(default_factory=dict)
    execution: dict = Field(default_factory=dict)
    provenance: dict = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    tool_calls: int = 0
    model_calls: int = 0
    model_tokens: int = 0
    elapsed_ms: float = 0
