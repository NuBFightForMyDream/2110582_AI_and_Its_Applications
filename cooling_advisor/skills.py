from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Skill:
    id: str
    owner: str
    name: str
    file: str
    tools: tuple[str, ...]
    version: str = "0.2.0"

    def instructions(self) -> str:
        return (Path(__file__).parent / "skill_specs" / self.file).read_text()


SKILLS = {
    "S1": Skill("S1", "supervisor", "Plan Analysis", "s1-plan.md", ("call_data_analyst", "call_cooling_advisor", "finish_case")),
    "S2": Skill("S2", "data_analyst", "Inspect Cooling Data", "s2-inspect.md", ("read_telemetry", "inspect_data_quality")),
    "S3": Skill("S3", "data_analyst", "Analyze Performance", "s3-analyze.md", ("compute_baseline", "finish_analysis")),
    "S4": Skill("S4", "cooling_advisor", "Evaluate Cooling Options", "s4-evaluate.md", ("lookup_experiments", "compare_candidates", "propose_option")),
    "S5": Skill("S5", "supervisor", "Compose Recommendation", "s5-compose.md", ()),
}
