"""Generate hand-authored synthetic fixtures. This does NOT run Sustain-LC."""
import json
from pathlib import Path


def experiment(scenario, suffix, setpoint, cooling, it, inlet, snapshot=None):
    def values(value):
        return value if isinstance(value, list) else [value] * 4
    return {
        "experiment_id": f"{scenario}-{suffix}", "scenario_id": scenario,
        "snapshot_id": snapshot or f"{scenario}-initial", "zone_id": "zone-a",
        "horizon_hours": 1.0, "energy_boundary": "synthetic_it_plus_cooling",
        "source_kind": "synthetic", "setpoint_c": setpoint,
        "samples": [{"step": i, "interval_hours": 0.25, "cooling_kw": values(cooling)[i],
                     "it_kw": values(it)[i], "max_inlet_c": values(inlet)[i]} for i in range(4)],
    }


def build():
    definitions = [
        ("steady", "Steady load · supported saving", "One safe alternative saves energy; a lower-energy option is rejected because it is too hot.", [
            experiment("steady", "baseline", 18, [40, 42, 44, 42], 100, [24.5, 24.7, 24.9, 24.8]),
            experiment("steady", "19", 19, [35, 36, 38, 35], 101, [25.2, 25.4, 25.6, 25.5]),
            experiment("steady", "20", 20, 30, 105, [27.8, 28.0, 28.4, 28.1]),
        ]),
        ("peak", "Peak load · keep baseline", "The existing setting passes; the alternative exceeds the demo temperature limit.", [
            experiment("peak", "baseline", 18, 60, 160, [25.5, 25.8, 26.0, 25.9]),
            experiment("peak", "19", 19, 54, 162, [27.5, 28.0, 28.2, 27.9]),
        ]),
        ("missing", "Missing temperature · blocked", "Maximum-inlet temperature is missing, so no setting recommendation can be verified.", [
            experiment("missing", "baseline", 18, 40, 100, None),
            experiment("missing", "19", 19, 35, 101, 25.5),
        ]),
        ("unsupported", "Unmatched experiment · blocked", "An alternative exists, but it started from a different snapshot and cannot be compared.", [
            experiment("unsupported", "baseline", 18, 40, 100, 25),
            experiment("unsupported", "19", 19, 30, 101, 25.5, snapshot="different-initial-state"),
        ]),
    ]
    return {"notice": "SYNTHETIC FIXTURES. Hand-authored demonstration, not measured data and not simulator exports.",
            "scenarios": [{"scenario_id": sid, "label": label, "description": description,
                           "zone_id": "zone-a", "snapshot_id": f"{sid}-initial",
                           "baseline_id": f"{sid}-baseline", "experiments": runs}
                          for sid, label, description, runs in definitions]}


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "cooling_advisor/data/scenarios.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"Wrote four synthetic demo scenarios to {target}")
