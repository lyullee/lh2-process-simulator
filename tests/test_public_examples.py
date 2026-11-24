"""Checks for the synthetic public application, not facility validation."""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from authoring_ui.server import build_bootstrap, run_public_scenario, validate_design
from lh2dt.simulation_observer import observe_steps


@pytest.mark.parametrize('scenario,sign', [('DEMO_STORAGE',0),('DEMO_FILL',1),('DEMO_VENT',-1)])
def test_boundary_mass_and_energy_balance(scenario, sign):
    design = build_bootstrap()['design']
    observed = []
    with observe_steps(lambda row: observed.append(row) if row else None):
        result = run_public_scenario(scenario, design)
    summary = result['summaries']['DEMO-TANK']
    assert summary['boundary_mass_kg'] == pytest.approx(sign * 0.001 * 2.0)
    assert abs(summary['mass_residual_kg']) < 1e-10
    assert abs(summary['energy_residual_J']) < 1e-5
    assert len(observed) == 10
    assert result['series']['DEMO-TANK'][-1]['time_s'] == pytest.approx(2.0)
    assert not result['facility_binding_ready']


def test_bootstrap_graph_and_all_templates_construct():
    boot=build_bootstrap()
    assert len(boot['design']['nodes'])==3
    assert validate_design(boot['design'])['issues']==[]
    for template in boot['library']['templates']:
        design=copy.deepcopy(boot['design'])
        node=design['nodes'][1]
        node.update(component_model=template.get('component_model'),module_id=template.get('module_id'),parameters=template['base_parameters'])
        assert validate_design(design)['issues']==[], template['template_id']


def test_scenario_respects_changed_volume_and_initial_condition():
    design=build_bootstrap()['design']
    original=run_public_scenario('DEMO_STORAGE',design)['summaries']['DEMO-TANK']
    design['nodes'][1]['parameters']['volume_m3'] *= 2
    changed=run_public_scenario('DEMO_STORAGE',design)['summaries']['DEMO-TANK']
    assert changed['final_mass_kg']==pytest.approx(2*original['final_mass_kg'])
    design['nodes'][1]['initial_conditions']['pressure_Pa_abs']=300000
    assert run_public_scenario('DEMO_STORAGE',design)['summaries']['DEMO-TANK']['final_pressure_Pa'] < original['final_pressure_Pa']


def test_fixed_demo_rejects_rewired_and_disabled_boundaries():
    design=build_bootstrap()['design']
    design['edges'][0]['target']='DEMO-OUTLET'
    with pytest.raises(ValueError,match='required demonstration connection'):
        run_public_scenario('DEMO_FILL',design)
    design=build_bootstrap()['design']
    design['nodes'][0]['initial_conditions']['mass_flow_kg_s']=float('nan')
    with pytest.raises(ValueError,match='boundary flow'):
        run_public_scenario('DEMO_FILL',design)
