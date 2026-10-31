"""Pinned SustainDC power/temperature submodel integration, isolated in a process."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .models import Experiment, Policy, Sample, Scenario, SimulationRequest

ROOT = Path(__file__).resolve().parent.parent
PINNED_COMMIT = 'a92b4755aca560e34a98d14028dda629eb968482'
REPO_URL = 'https://github.com/HewlettPackard/dc-rl.git'


def vendor_path():
    return Path(os.getenv('SUSTAINDC_PATH', str(ROOT / '.local/vendor/sustaindc'))).resolve()


def vendor_status():
    path = vendor_path()
    try:
        commit = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True, timeout=5, stderr=subprocess.DEVNULL).strip()
        clean = not subprocess.check_output(['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=no'], text=True, timeout=5, stderr=subprocess.DEVNULL).strip()
        return {'available': commit == PINNED_COMMIT and clean, 'commit': commit, 'clean': clean, 'path': str(path)}
    except Exception:
        return {'available': False, 'commit': None, 'clean': False, 'path': str(path)}


def install_vendor():
    path = vendor_path()
    if path.exists():
        if not vendor_status()['available']:
            raise RuntimeError('Existing simulator does not match the pinned clean revision; no files were reset')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'clone', REPO_URL, str(path)], check=True, timeout=180)
    subprocess.run(['git', '-C', str(path), 'checkout', '--detach', PINNED_COMMIT], check=True, timeout=30)


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_model(request: SimulationRequest, owner_id=None) -> Scenario:
    import numpy as np
    import pandas as pd
    status = vendor_status()
    if not status['available']:
        raise RuntimeError('Install the pinned simulator first: python -m cooling_advisor.simulation install')
    root = vendor_path()
    physics = _module('cooling_sustaindc_physics', root / 'envs/datacenter.py')
    config_module = _module('cooling_sustaindc_config', root / 'utils/dc_config_reader.py')
    cfg_path = root / 'utils/dc_config.json'
    cfg = config_module.DC_Config(dc_config_file=str(cfg_path), datacenter_capacity_mw=request.capacity_mw)
    # Upstream uses as_completed for rack construction, which can reorder racks.
    # Preserve the original configuration order explicitly, without modifying upstream.
    cfg.RACK_CPU_CONFIG = [
        [{'full_load_pwr': values[0], 'idle_pwr': values[-1]} for _ in range(int(cfg.CPUS_PER_RACK))]
        for values in cfg.DEFAULT_SERVER_POWER_CHARACTERISTICS
    ]
    sizing_ambient = {'AZ': 50.0, 'NY': 30.0, 'WA': 20.0}[request.city]
    np.random.seed(request.seed)
    cfg.CT_REFRENCE_AIR_FLOW_RATE, cfg.CT_FAN_REF_P = physics.chiller_sizing(
        cfg, min_CRAC_setpoint=15, max_CRAC_setpoint=21.6, max_ambient_temp=sizing_ambient)
    weather_name = {'AZ': 'USA_AZ_Phoenix-Sky.Harbor.epw', 'NY': 'USA_NY_New.York-LaGuardia.epw',
                    'WA': 'USA_WA_Seattle-Tacoma.epw'}[request.city]
    workload_name = {'alibaba1': 'Alibaba_CPU_Data_Hourly_1.csv', 'alibaba2': 'Alibaba_CPU_Data_Hourly_2.csv',
                     'google1': 'GoogleClusteData_CPU_Data_Hourly_1.csv'}[request.workload]
    weather_path = root / 'data/Weather' / weather_name
    workload_path = root / 'data/Workload' / workload_name
    weather = pd.read_csv(weather_path, skiprows=8, header=None).iloc[:, 6].to_numpy(dtype=float)
    loads = pd.read_csv(workload_path)['cpu_load'].to_numpy(dtype=float)
    start = (request.day_of_year - 1) * 24
    if start + request.hours > min(len(weather), len(loads)):
        raise ValueError('Requested trace window is unavailable')
    profile = [(float(loads[start + i // 4]), float(weather[start + i // 4])) for i in range(request.hours * 4)]
    if any(not 0 <= load <= 1 or not -50 <= ambient <= 60 for load, ambient in profile):
        raise ValueError('Upstream trace has invalid or missing values')
    provenance = {
        'simulator': 'SustainDC power-temperature submodel', 'upstream_repository': REPO_URL,
        'simulator_commit': PINNED_COMMIT, 'config_sha256': hashlib.sha256(cfg_path.read_bytes()).hexdigest(),
        'weather_file': weather_name, 'weather_sha256': hashlib.sha256(weather_path.read_bytes()).hexdigest(),
        'workload_file': workload_name, 'workload_sha256': hashlib.sha256(workload_path.read_bytes()).hexdigest(),
        'seed': request.seed, 'request': request.model_dump(),
        'temperature_metric': 'Modeled maximum of model.rackwise_inlet_temp (not an observed site sensor)',
        'energy_boundary': 'IT CPU + IT fans + CRAC fan + cooling-tower fan + chiller compressor + CW/CT pumps',
        'sampling': 'Hourly workload/weather held constant in four 15-minute modeled operating intervals',
        'trace_alignment': 'TMY weather and public workload paired by index; not a simultaneous real-facility recording',
        'adapter_adjustments': ['Rack CPU configurations constructed in source JSON order instead of thread-completion order',
                                'Electrical boundary includes modeled CRAC fan and CW/CT pumps omitted from upstream dc_total_power_kW'],
        'limitations': ['Static operating-point calculations; no modeled thermal inertia or closed-loop dynamics',
                        'Constant setpoint alternatives are idealized operating points, not executable transition plans',
                        'Defaults may make lower setpoints dominate; this is not proof that an AI controller beats a tuned baseline',
                        'Simulator output is not calibrated measured facility data or proof of operational safety'],
    }
    fingerprint = hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()
    scenario_id = 'sdc-' + hashlib.sha256((fingerprint + str(owner_id)).encode()).hexdigest()[:18]
    experiments = []
    for setting in request.candidate_setpoints:
        np.random.seed(request.seed)
        model = physics.DataCenter_ITModel(cfg.NUM_RACKS, cfg.RACK_SUPPLY_APPROACH_TEMP_LIST,
                                          cfg.RACK_CPU_CONFIG, cfg.MAX_W_PER_RACK, cfg)
        samples = []
        for step, (load, ambient) in enumerate(profile):
            cpu, fan, outlet = model.compute_datacenter_IT_load_outlet_temp([load * 100] * cfg.NUM_RACKS, setting)
            return_temp = physics.calculate_avg_CRAC_return_temp(cfg.RACK_RETURN_APPROACH_TEMP_LIST, outlet)
            crac, tower, thermal, compressor, cw, ct = physics.calculate_HVAC_power(
                setting, return_temp, ambient, sum(cpu) + sum(fan), cfg)
            components = dict(cpu=float(sum(cpu)/1000), it_fans=float(sum(fan)/1000),
                              crac_fan=float(crac/1000), tower_fan=float(tower/1000),
                              compressor=float(compressor/1000), cw_pump=float(cw/1000), ct_pump=float(ct/1000))
            samples.append(Sample(step=step, interval_hours=0.25, it_kw=components['cpu']+components['it_fans'],
                                  cooling_kw=sum(components[k] for k in ['crac_fan','tower_fan','compressor','cw_pump','ct_pump']),
                                  max_inlet_c=float(max(model.rackwise_inlet_temp)), max_outlet_c=float(max(outlet)),
                                  ambient_c=ambient, cpu_load_fraction=load, components_kw=components))
        experiments.append(Experiment(experiment_id=f'{scenario_id}-sp-{setting:g}', scenario_id=scenario_id,
                                      snapshot_id=fingerprint, zone_id='zone-a', horizon_hours=request.hours,
                                      energy_boundary='sustaindc_expanded_electrical_v1', source_kind='simulated',
                                      setpoint_c=setting, samples=samples, comparison_fingerprint=fingerprint))
    return Scenario(scenario_id=scenario_id, label=request.label,
                    description=f'{request.city} · {request.hours}h · {request.capacity_mw:g} MW configuration · pinned SustainDC physics',
                    zone_id='zone-a', snapshot_id=fingerprint, baseline_id=f'{scenario_id}-sp-{request.baseline_setpoint_c:g}',
                    experiments=experiments, owner_id=owner_id, provenance=provenance,
                    policy=Policy(version='sustaindc-study-v1', min_setpoint_c=15, max_setpoint_c=21.6,
                                  max_inlet_c=request.max_inlet_c))


def execute_simulation(request: SimulationRequest, owner_id=None, timeout=60) -> Scenario:
    """Hard timeout and process isolation; does not call a cloud service."""
    folder = ROOT / '.local' / 'simulation_jobs'
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder) as temporary:
        input_path, output_path = Path(temporary)/'input.json', Path(temporary)/'output.json'
        input_path.write_text(json.dumps({'request': request.model_dump(), 'owner_id': owner_id}))
        completed = subprocess.run([sys.executable, '-m', 'cooling_advisor.simulation_worker', str(input_path), str(output_path)],
                                   capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        if completed.returncode or not output_path.exists():
            raise RuntimeError('Simulator run failed. Verify pinned dependencies and source inputs; no scenario was imported.')
        return Scenario.model_validate_json(output_path.read_text())


if __name__ == '__main__':
    if sys.argv[1:] == ['install']:
        install_vendor()
        print('Pinned simulator is ready.')
    else:
        print('Usage: python -m cooling_advisor.simulation install')
