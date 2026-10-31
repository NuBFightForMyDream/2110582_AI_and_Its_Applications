import math

from .models import DemoPolicy, Experiment, Metrics


def quality_errors(run: Experiment) -> list[str]:
    errors = []
    if [s.step for s in run.samples] != list(range(len(run.samples))):
        errors.append("NON_CONTIGUOUS_INTERVALS")
    if not math.isclose(sum(s.interval_hours for s in run.samples), run.horizon_hours, abs_tol=1e-8):
        errors.append("INCOMPLETE_HORIZON")
    if any(s.max_inlet_c is None for s in run.samples):
        errors.append("MISSING_MAX_INLET_TEMPERATURE")
    return errors


def metrics(run: Experiment, policy: DemoPolicy) -> Metrics:
    cooling = sum(s.cooling_kw * s.interval_hours for s in run.samples)
    it = sum(s.it_kw * s.interval_hours for s in run.samples)
    inlet = max((s.max_inlet_c for s in run.samples if s.max_inlet_c is not None), default=None)
    errors = quality_errors(run)
    if inlet is not None and inlet > policy.max_inlet_c:
        errors.append("MAX_INLET_LIMIT_EXCEEDED")
    if not policy.min_setpoint_c <= run.setpoint_c <= policy.max_setpoint_c:
        errors.append("SETPOINT_OUT_OF_RANGE")
    return Metrics(experiment_id=run.experiment_id, setpoint_c=run.setpoint_c,
                   cooling_kwh=cooling, it_kwh=it, total_kwh=cooling + it,
                   max_inlet_c=inlet, feasible=not errors, reasons=errors,
                   components_kwh={key: sum(s.components_kw.get(key, 0) * s.interval_hours for s in run.samples)
                                   for key in sorted({key for s in run.samples for key in s.components_kw})})


def compatible(run: Experiment, baseline: Experiment) -> bool:
    fields = ("scenario_id", "snapshot_id", "zone_id", "horizon_hours", "energy_boundary", "source_kind", "comparison_fingerprint")
    return all(getattr(run, f) == getattr(baseline, f) for f in fields)


def compare(runs: list[Experiment], baseline: Experiment, policy: DemoPolicy) -> list[Metrics]:
    baseline_energy = metrics(baseline, policy).total_kwh
    rows = []
    for run in runs:
        item = metrics(run, policy)
        item.savings_pct = 100 * (baseline_energy - item.total_kwh) / baseline_energy if baseline_energy > 0 else None
        rows.append(item)
    return rows
