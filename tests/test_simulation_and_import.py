import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from cooling_advisor.analysis import metrics
from cooling_advisor.catalog import Catalog, save_scenario
from cooling_advisor.engine import run_analysis
from cooling_advisor.ingestion import ImportRequest, import_csv
from cooling_advisor.models import AnalysisRequest, SimulationRequest
from cooling_advisor.simulation import execute_simulation, PINNED_COMMIT, vendor_status
from cooling_advisor.storage import SQLiteStore, execute_case

CSV='timestamp,interval_hours,cooling_kw,it_kw,max_inlet_c,setpoint_c\n2026-01-01T00:00:00Z,0.25,40,100,24,18\n2026-01-01T00:15:00Z,0.25,42,102,24.5,18\n'


@pytest.mark.parametrize('row',['2026-01-01T00:00:00Z,0.25,40,100,24','2026-01-01T00:00:00Z,0.25,40,100,24,18,extra'])
def test_csv_rows_must_match_header_columns(row):
    header='timestamp,interval_hours,cooling_kw,it_kw,setpoint_c,max_inlet_c\n'
    with pytest.raises(ValueError,match='column count'):
        import_csv(ImportRequest(label='Ragged',csv_text=header+row+'\n'),'owner')


def test_bundled_upstream_studies_have_reproducible_provenance_and_energy():
    studies=[Catalog().get(row['scenario_id']) for row in Catalog().list() if row['source_kind']=='simulated']
    assert len(studies)>=2
    for scenario in studies:
        assert scenario.provenance['simulator_commit']==PINNED_COMMIT
        assert len(scenario.provenance['weather_sha256'])==64
        for run in scenario.experiments:
            m=metrics(run,scenario.policy)
            assert sum(m.components_kwh.values())==pytest.approx(m.total_kwh)
            assert len(run.samples)==run.horizon_hours*4
            assert m.max_inlet_c==pytest.approx(run.setpoint_c+5.3)
        result=run_analysis(AnalysisRequest(scenario_id=scenario.scenario_id))
        assert result.status=='completed'
        assert result.source_kind=='simulated'
        assert result.policy_version=='sustaindc-study-v1'
        assert result.limitations
        assert 'Synthetic' not in result.explanation


@pytest.mark.skipif(not vendor_status()['available'],reason='Pinned upstream simulator not installed')
def test_actual_isolated_upstream_model_is_repeatable_and_conserves_electrical_energy():
    request=SimulationRequest(hours=1,candidate_setpoints=[16,18],seed=9)
    first=execute_simulation(request,owner_id='operator-a')
    second=execute_simulation(request,owner_id='operator-a')
    assert first.model_dump()==second.model_dump()
    assert len(first.experiments)==2
    for run in first.experiments:
        for s in run.samples:
            assert sum(s.components_kw.values())==pytest.approx(s.cooling_kw+s.it_kw)
    assert first.experiments[0].samples[0].it_kw != first.experiments[1].samples[0].it_kw


def test_csv_is_observation_only_and_owner_scoped(tmp_path,monkeypatch):
    monkeypatch.setenv('COOLING_CATALOG_DIR',str(tmp_path/'catalog'))
    scenario=import_csv(ImportRequest(label='Meter baseline',csv_text=CSV),'alice')
    save_scenario(scenario,owner_id='alice')
    cat=Catalog(owner_id='alice')
    result=run_analysis(AnalysisRequest(scenario_id=scenario.scenario_id),catalog=cat)
    assert result.status=='not_supported'
    result=run_analysis(AnalysisRequest(scenario_id=scenario.scenario_id,mode='analyze'),catalog=cat)
    assert result.status=='completed'
    assert result.baseline.total_kwh==pytest.approx(71)
    assert result.source_kind=='measured'
    assert 'unverified' in scenario.provenance['origin']
    with pytest.raises(ValueError):
        Catalog(owner_id='bob').get(scenario.scenario_id)
    with pytest.raises(ValueError):
        save_scenario(scenario.model_copy(update={'label':'changed'}),owner_id='alice')
    save_scenario(scenario,owner_id='alice')  # exact replay is idempotent


@pytest.mark.parametrize('csv_text',[
    CSV.replace('00:15:00','00:30:00'),
    CSV.replace('00:15:00','00:00:00'),
    CSV.replace('Z',''),
    CSV.replace(',40,',',NaN,'),
    CSV.replace(',40,',',-1,'),
    CSV.replace('cooling_kw','cooling_kwh'),
    CSV.replace('24.5,18','24.5,19'),
])
def test_import_rejects_bad_units_intervals_and_values(csv_text):
    with pytest.raises((ValueError,ValidationError)):
        import_csv(ImportRequest(label='Invalid',csv_text=csv_text),'alice')


def test_cross_owner_case_and_review_access_is_denied(tmp_path):
    alice=SQLiteStore(tmp_path/'cases.db',owner_id='alice')
    bob=SQLiteStore(tmp_path/'cases.db',owner_id='bob')
    case=execute_case(AnalysisRequest(scenario_id='steady'),'same-key',alice)
    with pytest.raises(KeyError):
        bob.get(case.case_id)
    with pytest.raises(KeyError):
        bob.decision(case.case_id,'dismiss')
    assert bob.recent()==[]
    own=execute_case(AnalysisRequest(scenario_id='peak'),'same-key',bob)
    assert own.case_id!=case.case_id


def test_candidate_bounds_and_baseline_inclusion():
    req=SimulationRequest(baseline_setpoint_c=18.5,candidate_setpoints=[16,16,20])
    assert req.candidate_setpoints==[16,18.5,20]
    with pytest.raises(ValidationError):
        SimulationRequest(candidate_setpoints=[float('nan')])


def test_simulator_process_has_a_hard_timeout(tmp_path,monkeypatch):
    import subprocess
    with pytest.raises(subprocess.TimeoutExpired):
        execute_simulation(SimulationRequest(hours=1,candidate_setpoints=[18]),timeout=.001)
