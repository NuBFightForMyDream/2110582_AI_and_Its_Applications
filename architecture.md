# Cooling Advisor — Architecture

Version 0.2 implements a local Python workspace. Start with [Run the application](README.md), then read one component at a time. [Implementation status](architecture/prototype-status.md) distinguishes tested local behavior from integrations awaiting credentials and future deployment work.

| Component | Read first |
|---|---|
| Overall request/data flow | [Overview](architecture/overview/README.md) |
| Parent, children, skills and tool loops | [Agents](architecture/agents/README.md) |
| Request, tool and result checks | [Guardrails](architecture/guardrails/README.md) |
| SustainDC and CSV evidence | [Data](architecture/data/README.md) |
| Energy arithmetic and ranking | [Analysis](architecture/analysis/README.md) |
| FastAPI, owner scope and storage | [Backend](architecture/backend/README.md) |
| Streamlit workspace | [Dashboard](architecture/dashboard/README.md) |
| Events, budgets and usage | [Monitoring](architecture/monitoring/README.md) |
| Run/configure/deploy | [Deployment](architecture/deployment/README.md) |
| Remaining gaps and acceptance criteria | [Review](architecture/review/README.md) |

Implementation lives in `cooling_advisor/`; runtime skills live in `cooling_advisor/skill_specs/`. Previous designs and the pre-upgrade prototype are preserved under `archive/`. No knowledge/RAG component is used.
