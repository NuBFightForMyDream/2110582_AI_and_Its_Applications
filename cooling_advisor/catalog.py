"""Immutable, owner-scoped evidence catalog with validated local imports."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

from .models import Scenario

ROOT = Path(__file__).resolve().parent.parent
LOCAL_OWNER = 'local-demo-operator'


def catalog_directory():
    return Path(os.getenv('COOLING_CATALOG_DIR', str(ROOT / '.local/catalog')))


class Catalog:
    def __init__(self, scenarios: list[Scenario] | None = None, *, owner_id=LOCAL_OWNER, allowed_zones=('zone-a',)):
        self.owner_id = owner_id
        self.allowed_zones = frozenset(allowed_zones)
        if scenarios is None:
            scenarios = []
            for path in sorted((Path(__file__).parent/'data').glob('*.json')):
                payload = json.loads(path.read_text())
                scenarios.extend(Scenario.model_validate(row) for row in payload.get('scenarios', []))
            if catalog_directory().exists():
                for path in sorted(catalog_directory().glob('*.json')):
                    try:
                        scenario = Scenario.model_validate_json(path.read_text())
                    except (ValueError, OSError):
                        continue  # Partial/invalid imports are never made visible.
                    scenarios.append(scenario)
        self.scenarios = {s.scenario_id: s.model_copy(deep=True) for s in scenarios
                          if s.zone_id in self.allowed_zones and (s.owner_id is None or s.owner_id == owner_id)}

    def get(self, scenario_id: str) -> Scenario:
        if scenario_id not in self.scenarios:
            raise ValueError('UNKNOWN_OR_UNAUTHORIZED_SCENARIO')
        return self.scenarios[scenario_id].model_copy(deep=True)

    def list(self):
        result = []
        for scenario in self.scenarios.values():
            item = scenario.model_dump(exclude={'experiments'})
            item['source_kind'] = scenario.experiments[0].source_kind
            item['experiment_count'] = len(scenario.experiments)
            item['horizon_hours'] = scenario.experiments[0].horizon_hours
            result.append(item)
        return sorted(result, key=lambda row: (row['source_kind'] == 'synthetic', row['label']))


def save_scenario(scenario: Scenario, *, owner_id=LOCAL_OWNER):
    scenario = scenario.model_copy(update={'owner_id': owner_id}, deep=True)
    scenario = Scenario.model_validate(scenario.model_dump())
    path = catalog_directory() / (hashlib.sha256((str(owner_id)+scenario.scenario_id).encode()).hexdigest()+'.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = scenario.model_dump_json(indent=2)
    # Write complete content first, then atomically link it into the immutable catalog.
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix='.pending')
    try:
        with os.fdopen(descriptor,'w') as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(temporary,path)
        except FileExistsError:
            if path.read_text() != payload:
                raise ValueError('Evidence ID already exists with different content; imports are immutable')
    finally:
        os.unlink(temporary)
    return scenario
