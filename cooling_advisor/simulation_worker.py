import json
from pathlib import Path
import sys

from .models import SimulationRequest
from .simulation import run_model

if __name__ == '__main__':
    source = json.loads(Path(sys.argv[1]).read_text())
    scenario = run_model(SimulationRequest.model_validate(source['request']), source.get('owner_id'))
    Path(sys.argv[2]).write_text(scenario.model_dump_json(indent=2))
