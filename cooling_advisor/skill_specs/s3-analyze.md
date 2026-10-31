# S3 — Analyze Performance
Owner: data_analyst. Version: 0.2.0.

Use compute_baseline after a successful quality check. Python integrates interval-average kW × hours into kWh and reports cooling, IT and component energy separately. Use finish_analysis only after the quality check and required calculations, or to report missing evidence. Preserve IDs and units. Never manufacture measurements or silently fill missing temperatures.

Tools: compute_baseline, finish_analysis. Measured/imported, simulated and synthetic evidence must be described according to provenance. A study threshold is not a site-approved operating limit.
