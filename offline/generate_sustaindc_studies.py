"""Regenerate the included studies from pinned upstream physics; never fabricate outputs."""
import json
from pathlib import Path
from cooling_advisor.models import SimulationRequest
from cooling_advisor.simulation import execute_simulation


def main():
    runs=[]
    for params in [dict(label='Phoenix · summer workload',city='AZ',day_of_year=182),
                   dict(label='New York · winter workload',city='NY',day_of_year=15,workload='google1')]:
        scenario=execute_simulation(SimulationRequest(**params))
        runs.append(scenario.model_dump(mode='json'))
        print('Generated',scenario.scenario_id)
    target=Path(__file__).resolve().parents[1]/'cooling_advisor/data/sustaindc_runs.json'
    target.write_text(json.dumps({'scenarios':runs},indent=2))


if __name__=='__main__':
    main()
