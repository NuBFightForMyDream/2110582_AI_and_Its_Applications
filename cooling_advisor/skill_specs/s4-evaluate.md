# S4 — Evaluate Cooling Options
Owner: cooling_advisor. Version: 0.2.0.

Call lookup_experiments, then compare_candidates, then propose_option. Matching requires scenario, snapshot, zone, horizon, source, energy boundary and comparison fingerprint. Include baseline in the supported comparison. Choose the feasible minimum-total-kWh evidence ID, breaking ties by experiment ID. Propose null if none is feasible. Never provide numerical estimates in place of tool evidence.

G3 independently rereads the catalog, recomputes metrics and checks your proposal. No matched alternatives means not_supported; an optimal baseline means no_change. No physical control or closed-loop performance claim follows from a static simulator comparison.
