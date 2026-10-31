"""Bounded CSV importer; measured observations never become counterfactual runs."""
import csv
from datetime import datetime
import hashlib
import io
import json

from .models import Experiment, Policy, Sample, Scenario, StrictModel
from pydantic import Field


class ImportRequest(StrictModel):
    label: str = Field(min_length=1, max_length=80)
    filename: str = Field(default='telemetry.csv', max_length=100)
    csv_text: str = Field(min_length=1, max_length=2_000_000)


REQUIRED = {'timestamp','interval_hours','cooling_kw','it_kw','max_inlet_c','setpoint_c'}


def import_csv(request: ImportRequest, owner_id) -> Scenario:
    reader = csv.DictReader(io.StringIO(request.csv_text))
    if not REQUIRED.issubset(set(reader.fieldnames or [])):
        raise ValueError('CSV requires: '+', '.join(sorted(REQUIRED)))
    rows = []
    for row in reader:
        if len(rows) >= 672:
            raise ValueError('At most 672 rows per observation window')
        # DictReader fills short rows with None and collects extra cells under the None key.
        if None in row or any(row[key] is None for key in REQUIRED):
            raise ValueError(f'CSV row {reader.line_num} does not match the header column count')
        stamp = datetime.fromisoformat(row['timestamp'].replace('Z','+00:00'))
        if stamp.utcoffset() is None:
            raise ValueError('Timestamps must include a UTC offset')
        rows.append((stamp,row))
    if not rows:
        raise ValueError('CSV has no observations')
    rows.sort(key=lambda pair: pair[0])
    setting = float(rows[0][1]['setpoint_c'])
    samples = []
    for i,(stamp,row) in enumerate(rows):
        if float(row['setpoint_c']) != setting:
            raise ValueError('One imported baseline window must have a constant setpoint')
        interval = float(row['interval_hours'])
        if i and abs((stamp-rows[i-1][0]).total_seconds()/3600 - float(rows[i-1][1]['interval_hours'])) > 1e-6:
            raise ValueError('Timestamps must be unique and contiguous with declared intervals')
        samples.append(Sample(step=i, interval_hours=interval, cooling_kw=float(row['cooling_kw']),
                              it_kw=float(row['it_kw']), max_inlet_c=float(row['max_inlet_c']) if row['max_inlet_c'].strip() else None))
    digest = hashlib.sha256(request.csv_text.encode()).hexdigest()
    sid = 'csv-' + hashlib.sha256((str(owner_id)+digest).encode()).hexdigest()[:18]
    run = Experiment(experiment_id=sid+'-baseline', scenario_id=sid, snapshot_id=digest, zone_id='zone-a',
                     horizon_hours=sum(s.interval_hours for s in samples), energy_boundary='user_declared_it_plus_cooling',
                     source_kind='measured', setpoint_c=setting, samples=samples, comparison_fingerprint=digest)
    provenance = {'origin':'user-imported CSV; source claims are unverified', 'source_verification':'unverified', 'filename':request.filename,
                  'artifact_sha256':digest, 'start_utc':rows[0][0].isoformat(),
                  'sample_semantics':'User-declared interval-average kW', 'temperature_metric':'User-declared maximum inlet °C',
                  'limitations':['Observational data supports analysis only; no matched counterfactual settings are supplied',
                                 'Units, sensor calibration and completeness require source verification']}
    return Scenario(scenario_id=sid,label=request.label,description='Imported observation window · analysis only',
                    zone_id='zone-a',snapshot_id=digest,baseline_id=run.experiment_id,experiments=[run],owner_id=owner_id,
                    comparison_enabled=False,provenance=provenance,
                    policy=Policy(version='import-observation-v1',min_setpoint_c=10,max_setpoint_c=35))


def persist_artifact(request: ImportRequest, owner_id):
    from .catalog import catalog_directory
    from pathlib import Path
    import os
    import tempfile
    folder = catalog_directory().parent / 'artifacts' / hashlib.sha256(str(owner_id).encode()).hexdigest()
    folder.mkdir(parents=True,exist_ok=True)
    digest = hashlib.sha256(request.csv_text.encode()).hexdigest()
    path = folder / (digest+'.csv')
    descriptor, temporary = tempfile.mkstemp(dir=folder)
    try:
        with os.fdopen(descriptor,'w') as file:
            file.write(request.csv_text)
        try:
            os.link(temporary,path)
        except FileExistsError:
            if path.read_text() != request.csv_text:
                raise ValueError('Artifact hash collision')
    finally:
        os.unlink(temporary)
    return digest
