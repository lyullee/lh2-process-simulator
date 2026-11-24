"""Dependency-free local web server for the LH2 process authoring prototype.

The browser owns only presentation state. Component validation and simulations
are executed in Python with the existing :mod:`lh2dt` first-principles models.
"""

from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import inspect
import json
import math
from pathlib import Path
import re
import sys
import os
import threading
import uuid
from typing import Any, Mapping
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
STATIC = Path(__file__).resolve().parent / "static"
DESIGN_DIR = Path(os.environ.get("LH2_SIMULATOR_DATA_DIR", str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LH2ProcessSimulatorPublic"))) / "designs"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from lh2dt.authoring import load_component_library  # noqa: E402
from lh2dt.catalog import REQUIRED_PARAMETERS  # noqa: E402
from lh2dt.component_factory import build_asset_model  # noqa: E402
from lh2dt.connection_geometry import ImpulseLineConnectionGeometry  # noqa: E402
from lh2dt.common_pressure import CommonPressureRigidVolume  # noqa: E402
from lh2dt.control import PIDController, PIDParameters  # noqa: E402
from lh2dt.dpt_observation import ImpulseLineHydrostaticDP, TankDptObservation  # noqa: E402
from lh2dt.geometry import HorizontalVesselGeometry, HydrostaticDifferentialPressure  # noqa: E402
from lh2dt.instrumentation import FirstOrderTransmitter, TransmitterParameters  # noqa: E402
from lh2dt.hart import PMD75BHartObservation  # noqa: E402
from lh2dt.natural_circulation import CirculationLegGeometry, NaturalCirculationLoop  # noqa: E402
from lh2dt.network import SteadyNetwork  # noqa: E402
from lh2dt.ortho_para import OrthoParaKineticsParameters  # noqa: E402
from lh2dt.phase_change import (  # noqa: E402
    kinetic_gas_film_heat_transfer_coefficient,
    schrage_mass_flux,
)
from lh2dt.properties import HydrogenProperties  # noqa: E402
from lh2dt.safety_interlock import EmergencyShutdownInterlock  # noqa: E402
from lh2dt.streams import MassEnergyFlow, mix_streams, split_stream  # noqa: E402
from lh2dt.tank import HomogeneousTank, TankGeometry  # noqa: E402
from lh2dt.simulation_observer import observe_steps, emit_step  # noqa: E402
from lh2dt.vacuum_insulation import VacuumInsulation, VacuumInsulationParameters  # noqa: E402
from lh2dt.vacuum_jacketed_pipe import VacuumJacketedPipeEnvelope  # noqa: E402


LIBRARY_PATH = ROOT / "configs" / "public_component_library.json"


def _aux_template(
    template_id: str,
    module_id: str,
    display_name: str,
    category: str,
    parameters: Mapping[str, Any],
    *,
    kind: str = "module",
    ports: tuple[Mapping[str, str], ...] = (),
) -> dict[str, Any]:
    return {
        "template_id": template_id,
        "module_id": module_id,
        "component_model": None,
        "display_name": display_name,
        "category": category,
        "kind": kind,
        "base_parameters": dict(parameters),
        "scale_exponents": {},
        "ports": [dict(item) for item in ports],
        "parameter_source": "existing lh2dt reusable module; explicit authoring inputs",
        "integration_scope": "auxiliary_physics_or_control",
    }


SIGNAL_PORTS = (
    {"port_id": "IN", "medium": "signal", "direction": "inlet", "role": "signal_input"},
    {"port_id": "OUT", "medium": "signal", "direction": "outlet", "role": "signal_output"},
)
HART_PORTS = (
    {"port_id": "DPT-IN", "medium": "signal", "direction": "inlet", "role": "differential_pressure_input"},
    {"port_id": "PV", "medium": "signal", "direction": "outlet", "role": "primary_variable"},
    {"port_id": "SV", "medium": "signal", "direction": "outlet", "role": "secondary_variable"},
    {"port_id": "TV", "medium": "signal", "direction": "outlet", "role": "tertiary_variable"},
    {"port_id": "QV", "medium": "signal", "direction": "outlet", "role": "quaternary_variable"},
)
MIXER_PORTS = (
    {"port_id": "IN-1", "medium": "hydrogen", "direction": "inlet", "role": "material_inlet"},
    {"port_id": "IN-2", "medium": "hydrogen", "direction": "inlet", "role": "material_inlet"},
    {"port_id": "OUT", "medium": "hydrogen", "direction": "outlet", "role": "material_outlet"},
)
SPLITTER_PORTS = (
    {"port_id": "IN", "medium": "hydrogen", "direction": "inlet", "role": "material_inlet"},
    {"port_id": "OUT-1", "medium": "hydrogen", "direction": "outlet", "role": "material_outlet"},
    {"port_id": "OUT-2", "medium": "hydrogen", "direction": "outlet", "role": "material_outlet"},
)
THERMAL_PORTS = (
    {"port_id": "WARM", "medium": "thermal", "direction": "inlet", "role": "warm_boundary"},
    {"port_id": "COLD", "medium": "thermal", "direction": "outlet", "role": "cold_boundary"},
)

AUXILIARY_TEMPLATES = (
    _aux_template("para-hydrogen-property-package", "hydrogen_properties", "ParaHydrogen 물성 패키지", "Property & Solvers", {}, kind="property"),
    _aux_template("vacuum-insulation-boundary", "vacuum_insulation", "진공단열 열경계", "Thermal Physics", {
        "area_m2": 50.0, "effective_emissivity": 0.03,
        "mli_solid_conductance_W_K": 0.02, "support_conductance_W_K": 0.08,
        "residual_gas_coefficient_W_m2K_Pa": 0.002,
        "maximum_valid_vacuum_pressure_Pa": 1.0,
    }, kind="thermal", ports=THERMAL_PORTS),
    _aux_template("vacuum-jacketed-pipe-envelope", "vacuum_jacketed_pipe", "진공재킷 배관 형상", "Thermal Physics", {
        "length_m": 10.0, "process_inner_diameter_m": 0.025,
        "process_outer_diameter_m": 0.034, "jacket_inner_diameter_m": 0.08,
    }, kind="pipe"),
    _aux_template("first-order-transmitter", "transmitter_observation", "1차 지연 트랜스미터", "Instrumentation & Control", {
        "lower_range": 0.0, "upper_range": 1000000.0, "time_constant_s": 1.0,
        "scale_factor": 1.0, "bias": 0.0, "resolution": 100.0,
        "accuracy_fraction_of_span": 0.001,
    }, kind="instrument", ports=SIGNAL_PORTS),
    _aux_template("tank-dpt-hart-observation", "tank_dpt_observation", "탱크 DPT·HART 관측", "Instrumentation & Control", {
        "lower_tap_height_m": 0.2, "upper_tap_height_m": 2.2,
        "sensor_height_m": 1.0, "high_leg_density_kg_m3": 70.0,
        "low_leg_density_kg_m3": 1.0, "position_adjustment_Pa": 0.0,
    }, kind="instrument", ports=SIGNAL_PORTS),
    _aux_template("pmd75b-hart-dynamic-variables", "pmd75b_hart", "PMD75B HART PV/SV/TV/QV", "Instrumentation & Control", {}, kind="instrument", ports=HART_PORTS),
    _aux_template("impulse-line-connection-geometry", "impulse_line_geometry", "DPT 임펄스라인 체적·열용량", "Piping", {
        "inner_diameter_m": 0.006, "outer_diameter_m": 0.01,
        "route_length_m": 2.0, "line_count": 2,
        "wall_density_kg_m3": 7950.0, "wall_specific_heat_J_kgK": 500.0,
    }, kind="pipe"),
    _aux_template("hydrogen-stream-mixer", "stream_mixer", "수소 질량·엔탈피 혼합기", "Flow Routing", {
        "inlet_mass_flows_kg_s": [0.1, 0.1],
        "inlet_enthalpies_J_kg": [100000.0, 120000.0],
    }, kind="mixer", ports=MIXER_PORTS),
    _aux_template("hydrogen-stream-splitter", "stream_splitter", "수소 보존 분배기", "Flow Routing", {
        "inlet_mass_flow_kg_s": 0.2, "inlet_enthalpy_J_kg": 110000.0,
        "split_fractions": [0.5, 0.5],
    }, kind="splitter", ports=SPLITTER_PORTS),
    _aux_template("pid-controller", "pid_control", "PID 제어기", "Instrumentation & Control", {
        "proportional_gain": 1.0, "integral_gain_per_s": 0.01, "derivative_gain_s": 0.0,
        "output_minimum": 0.0, "output_maximum": 1.0, "output_bias": 0.0,
        "derivative_filter_time_s": 0.0, "reverse_acting": False,
    }, kind="control", ports=SIGNAL_PORTS),
    _aux_template("emergency-shutdown-interlock", "safety_interlock", "ESD 인터록", "Instrumentation & Control", {
        "trip_alarm_count": 2, "manual_reset_required": True,
    }, kind="control", ports=SIGNAL_PORTS),
    _aux_template("natural-circulation-loop", "natural_circulation", "자연순환 루프", "Advanced Physics", {
        "vertical_separation_m": 1.0, "leg_length_m": 2.0,
        "hydraulic_diameter_m": 0.025, "flow_area_m2": 0.00049087385,
        "roughness_m": 1.0e-6, "local_loss_coefficient": 1.0,
    }, kind="physics"),
    _aux_template("common-pressure-rigid-volume", "common_pressure", "공통압력 강체적 DAE", "Advanced Physics", {
        "total_volume_m3": 1.0,
    }, kind="physics"),
    _aux_template("ortho-para-conversion", "ortho_para", "오르토·파라 전환", "Advanced Physics", {
        "conversion_rate_constant_s": 0.0, "conversion_enthalpy_J_kg": 0.0,
        "rotational_temperature_K": 85.4, "maximum_rotational_level": 24,
    }, kind="physics"),
    _aux_template("schrage-phase-change", "schrage_phase_change", "Schrage 상변화", "Advanced Physics", {
        "accommodation_coefficient": 0.001,
    }, kind="physics"),
    _aux_template("kinetic-gas-film", "kinetic_gas_film", "분자운동 기상막", "Advanced Physics", {
        "energy_accommodation_coefficient": 1.0,
    }, kind="physics"),
    _aux_template("dynamic-network-solver", "dynamic_network", "동적 네트워크 해석기", "Property & Solvers", {}, kind="solver"),
    _aux_template("steady-network-solver", "steady_network", "정상 압력·엔탈피망 해석기", "Property & Solvers", {}, kind="solver"),
)


SCENARIO_MODULES = {"DEMO_STORAGE": "storage", "DEMO_FILL": "fill", "DEMO_VENT": "vent"}
SCENARIO_EDGES = {"DEMO_STORAGE": [], "DEMO_FILL": ["DEMO-LINE-IN"], "DEMO_VENT": ["DEMO-LINE-OUT"]}

def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def initial_condition_defaults(
    kind: str,
    component_model: str | None = None,
    module_id: str | None = None,
    node_id: str = "",
) -> dict[str, Any]:
    """Return editable, SI-unit initial conditions for an authored unit."""

    model = str(component_model or module_id or "").lower()
    kind = str(kind or "").lower()
    name = str(node_id).upper()
    if kind in {"tank", "receiver"} or "tank" in model:
        pressure = 650_000.0 if kind == "receiver" else 400_000.0
        wall_temperature = 28.5 if kind == "receiver" else 26.0
        return {
            "enabled": True,
            "pressure_Pa_abs": pressure,
            "liquid_fill_fraction": 0.5,
            "fluid_temperature_K": 20.0,
            "wall_temperature_K": wall_temperature,
            "lower_wall_temperature_K": wall_temperature,
            "upper_wall_temperature_K": wall_temperature,
        }
    if kind == "boundary":
        is_gas = "GH2" in name
        pressure = 200_000.0 if "DEMAND" in name or "SUPPLY" in name else 800_000.0
        return {
            "enabled": True,
            "pressure_Pa_abs": pressure,
            "temperature_K": 300.0 if is_gas else 20.0,
            "mass_flow_kg_s": 0.0,
            "vapor_quality": 1.0 if is_gas else 0.0,
        }
    if kind == "header":
        pressure = 300_000.0
        if "RECEIVING" in name:
            pressure = 500_000.0
        elif "BOG" in name:
            pressure = 800_000.0
        elif "VENT" in name:
            pressure = 250_000.0
        elif "RELIQUEFACTION-RETURN" in name:
            pressure = 450_000.0
        return {
            "enabled": True,
            "pressure_Pa_abs": pressure,
            "temperature_K": 80.0 if "BOG" in name or "VENT" in name else 20.0,
            "mass_flow_kg_s": 0.0,
        }
    if kind in {"pump", "compressor"} or "pump" in model or "compressor" in model:
        return {
            "enabled": True,
            "speed_rpm": 10_000.0,
            "inlet_pressure_Pa_abs": 300_000.0,
            "inlet_temperature_K": 20.0,
            "initial_mass_flow_kg_s": 0.0,
        }
    if kind == "valve" or "valve" in model:
        return {"enabled": True, "opening_fraction": 1.0}
    if kind == "vaporizer" or "vaporizer" in model:
        inlet_pressure = 300_000.0
        return {
            "enabled": True,
            "inlet_pressure_Pa_abs": inlet_pressure,
            "inlet_temperature_K": 20.0,
            "wall_temperature_K": 293.15,
            "ambient_temperature_K": 293.15,
            "initial_mass_flow_kg_s": 0.0,
        }
    if kind == "reliquefier" or "reliquefier" in model:
        return {
            "enabled": True,
            "inlet_pressure_Pa_abs": 550_000.0,
            "inlet_temperature_K": 80.0,
            "cold_mass_temperature_K": 80.0,
            "load_fraction": 1.0,
            "initial_mass_flow_kg_s": 0.0,
        }
    if kind in {"pipe", "vent", "mixer", "splitter", "physics", "thermal"}:
        pressure = 101_325.0 if kind == "vent" else 300_000.0
        return {
            "enabled": True,
            "pressure_Pa_abs": pressure,
            "temperature_K": 80.0 if kind == "vent" else 20.0,
            "initial_mass_flow_kg_s": 0.0,
        }
    if kind == "instrument":
        return {"enabled": True, "initial_input": 0.0, "initial_output": 0.0}
    if kind == "control":
        return {"enabled": True, "setpoint": 0.0, "initial_output": 0.0}
    return {"enabled": True}


@lru_cache(maxsize=1)
def _library_payload() -> dict[str, Any]:
    payload = _read_json(LIBRARY_PATH)
    payload["templates"] = [*payload["templates"], *AUXILIARY_TEMPLATES]
    for template in payload["templates"]:
        template.setdefault(
            "initial_conditions",
            initial_condition_defaults(
                str(template.get("kind") or ""),
                template.get("component_model"),
                template.get("module_id"),
            ),
        )
    payload["unit_operation_template_count"] = len(payload["templates"]) - len(AUXILIARY_TEMPLATES)
    payload["auxiliary_template_count"] = len(AUXILIARY_TEMPLATES)
    return payload


def _template_payload(template_id: str | None) -> dict[str, Any] | None:
    if not template_id:
        return None
    for item in _library_payload()["templates"]:
        if item["template_id"] == template_id:
            return item
    raise KeyError(template_id)


def build_bootstrap() -> dict[str, Any]:
    library = _library_payload()
    template = next(t for t in library['templates'] if t.get('component_model') == 'homogeneous_tank')
    nodes = [
        dict(id='DEMO-SOURCE', label='가상 유입 경계', description='지정한 유입량; 배관망 해석값 아님', kind='boundary', template_id=None, component_model=None, capacity_scale=1.0, parameters={}, initial_conditions={'enabled':True,'mass_flow_kg_s':0.001}, ports=[], x=100, y=280, locked=False),
        dict(id='DEMO-TANK', label='가상 LH2 탱크', description='공개용 가상 탱크', kind='tank', template_id=template['template_id'], component_model='homogeneous_tank', capacity_scale=1.0, parameters=dict(template['base_parameters']), initial_conditions=initial_condition_defaults('tank','homogeneous_tank'), ports=list(template['ports']), x=430, y=280, locked=False),
        dict(id='DEMO-OUTLET', label='가상 유출 경계', description='지정한 유출량; 안전밸브 모형 아님', kind='boundary', template_id=None, component_model=None, capacity_scale=1.0, parameters={}, initial_conditions={'enabled':True,'mass_flow_kg_s':0.001}, ports=[], x=780, y=280, locked=False),
    ]
    edges = [dict(id='DEMO-LINE-IN', source='DEMO-SOURCE', target='DEMO-TANK', service='prescribed liquid inflow', enabled=True, show_valve=True, route=[[100,280],[430,280]]), dict(id='DEMO-LINE-OUT', source='DEMO-TANK', target='DEMO-OUTLET', service='prescribed homogeneous outflow', enabled=True, show_valve=True, route=[[430,280],[780,280]])]
    labels = {'DEMO_STORAGE':'가상 예제: 폐쇄 저장','DEMO_FILL':'가상 예제: 지정 유입','DEMO_VENT':'가상 예제: 지정 유출'}
    return {
        'schema_version':'authoring-ui-0.1',
        'design':{'design_id':'PUBLIC-SYNTHETIC-DEMO','title':'가상 LH2 공정 예제','basis':'Invented three-node teaching graph; no facility data','nodes':nodes,'edges':edges,'process_conditions':{'duration_s':2.0,'time_step_s':0.2,'ambient_temperature_K':295.0},'view':{'width':1181,'height':705,'zoom':1.0}},
        'library':library,
        'scenarios':[{'scenario_id':sid,'label':label,'active_edges':SCENARIO_EDGES[sid]} for sid,label in labels.items()],
        'units':{'pressure':'MPa(a)','temperature':'K','mass_flow':'kg/s'},
        'model_coverage':{'catalog_model_count':len(REQUIRED_PARAMETERS),'catalog_models':sorted(REQUIRED_PARAMETERS),'template_model_count':5,'auxiliary_template_count':len(AUXILIARY_TEMPLATES),'auxiliary_module_count':len(AUXILIARY_TEMPLATES),'auxiliary_modules':[t['module_id'] for t in AUXILIARY_TEMPLATES],'property_package':'CoolProp ParaHydrogen','dynamic_solver':'HomogeneousTank RK4','scenario_engine':'public prescribed-boundary examples','missing_catalog_models':sorted(set(REQUIRED_PARAMETERS)-{t['component_model'] for t in library['templates'] if t.get('component_model')})},
        'policy':{'physics':'first-principles homogeneous tank','facility_binding_ready':False,'screening_parameters':True,'fitting_used':False},
    }


def _validate_auxiliary_module(module_id: str, parameters: Mapping[str, Any]) -> None:
    p = dict(parameters)
    if module_id == "hydrogen_properties":
        HydrogenProperties()
    elif module_id == "vacuum_insulation":
        VacuumInsulation(VacuumInsulationParameters(**p))
    elif module_id == "vacuum_jacketed_pipe":
        VacuumJacketedPipeEnvelope(**p)
    elif module_id == "transmitter_observation":
        FirstOrderTransmitter(TransmitterParameters(**p))
    elif module_id == "pid_control":
        PIDController(PIDParameters(**p))
    elif module_id == "safety_interlock":
        EmergencyShutdownInterlock(
            int(p["trip_alarm_count"]),
            manual_reset_required=bool(p["manual_reset_required"]),
        )
    elif module_id == "tank_dpt_observation":
        taps = HydrostaticDifferentialPressure(
            float(p["lower_tap_height_m"]),
            float(p["upper_tap_height_m"]),
        )
        TankDptObservation(ImpulseLineHydrostaticDP(
            taps=taps,
            sensor_height_m=float(p["sensor_height_m"]),
            high_leg_density_kg_m3=float(p["high_leg_density_kg_m3"]),
            low_leg_density_kg_m3=float(p["low_leg_density_kg_m3"]),
            position_adjustment_Pa=float(p["position_adjustment_Pa"]),
        ))
    elif module_id == "pmd75b_hart":
        observation = PMD75BHartObservation()
        state = observation.initialize(1000.0)
        observation.advance(
            state,
            raw_sensor_pressure=1000.0,
            position_adjusted_pressure=1000.0,
            sensor_temperature=293.15,
            electronics_temperature=295.15,
            time_step_s=1.0,
        )
    elif module_id == "impulse_line_geometry":
        ImpulseLineConnectionGeometry(**p).as_layered_tank_connection()
    elif module_id == "stream_mixer":
        flows = tuple(float(value) for value in p["inlet_mass_flows_kg_s"])
        enthalpies = tuple(float(value) for value in p["inlet_enthalpies_J_kg"])
        if len(flows) != len(enthalpies):
            raise ValueError("mixer mass-flow and enthalpy arrays must have equal length")
        mix_streams(
            MassEnergyFlow(flow, enthalpy, source=f"IN-{index + 1}")
            for index, (flow, enthalpy) in enumerate(zip(flows, enthalpies))
        )
    elif module_id == "stream_splitter":
        split_stream(
            MassEnergyFlow(
                float(p["inlet_mass_flow_kg_s"]),
                float(p["inlet_enthalpy_J_kg"]),
                source="IN",
            ),
            tuple(float(value) for value in p["split_fractions"]),
        )
    elif module_id == "natural_circulation":
        leg = CirculationLegGeometry(
            length_m=float(p["leg_length_m"]),
            hydraulic_diameter_m=float(p["hydraulic_diameter_m"]),
            flow_area_m2=float(p["flow_area_m2"]),
            roughness_m=float(p["roughness_m"]),
            local_loss_coefficient=float(p["local_loss_coefficient"]),
        )
        NaturalCirculationLoop(float(p["vertical_separation_m"]), leg, leg)
    elif module_id == "common_pressure":
        CommonPressureRigidVolume(float(p["total_volume_m3"]))
    elif module_id == "ortho_para":
        OrthoParaKineticsParameters(
            conversion_rate_constant_s=float(p["conversion_rate_constant_s"]),
            conversion_enthalpy_J_kg=float(p["conversion_enthalpy_J_kg"]),
            rotational_temperature_K=float(p["rotational_temperature_K"]),
            maximum_rotational_level=int(p["maximum_rotational_level"]),
        )
    elif module_id == "schrage_phase_change":
        schrage_mass_flux(
            interface_pressure_Pa=120_000.0,
            interface_temperature_K=21.0,
            vapor_pressure_Pa=119_000.0,
            vapor_temperature_K=22.0,
            specific_gas_constant_J_kgK=4124.0,
            accommodation_coefficient=float(p["accommodation_coefficient"]),
        )
    elif module_id == "kinetic_gas_film":
        kinetic_gas_film_heat_transfer_coefficient(
            pressure_Pa=120_000.0,
            temperature_K=22.0,
            isobaric_heat_capacity_J_kgK=14_000.0,
            specific_gas_constant_J_kgK=4124.0,
            energy_accommodation_coefficient=float(
                p["energy_accommodation_coefficient"]
            ),
        )
    elif module_id == "steady_network":
        SteadyNetwork()
    elif module_id in {"dynamic_network", "scenario_execution", "accident_release"}:
        importlib.import_module(f"lh2dt.{module_id}")
    else:
        raise ValueError(f"unknown auxiliary module: {module_id}")


def validate_design(payload: Mapping[str, Any]) -> dict[str, Any]:
    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("nodes and edges must be lists")
    node_ids = [str(item.get("id", "")) for item in nodes]
    duplicate_nodes = sorted({item for item in node_ids if node_ids.count(item) > 1})
    known = set(node_ids)
    issues: list[str] = []
    warnings: list[str] = []
    process_conditions = payload.get("process_conditions", {})
    if process_conditions and not isinstance(process_conditions, Mapping):
        issues.append("공통 공정조건은 객체여야 함")
    elif isinstance(process_conditions, Mapping):
        for key in ("duration_s", "time_step_s", "ambient_temperature_K"):
            if key not in process_conditions:
                continue
            value = process_conditions[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
                issues.append(f"공통 공정조건 {key}는 0보다 큰 유한한 수여야 함")
        if (
            isinstance(process_conditions.get("duration_s"), (int, float))
            and isinstance(process_conditions.get("time_step_s"), (int, float))
            and float(process_conditions["time_step_s"]) > float(process_conditions["duration_s"])
        ):
            issues.append("공통 시간간격은 계산시간보다 클 수 없음")
    if duplicate_nodes:
        issues.append("중복 노드: " + ", ".join(duplicate_nodes))
    ready_components = 0
    model_components = 0
    ready_modules = 0
    auxiliary_modules = 0
    for node in nodes:
        initial = node.get("initial_conditions")
        if not isinstance(initial, Mapping):
            warnings.append(f"{node.get('id')}: 초기조건 없음(불러올 때 기본값으로 자동 보완됨)")
        else:
            for key, value in initial.items():
                if isinstance(value, bool):
                    continue
                if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    issues.append(f"{node.get('id')}: 초기조건 {key}가 유한한 수가 아님")
            pressure_keys = [key for key in initial if "pressure_Pa" in str(key)]
            if any(float(initial[key]) <= 0 for key in pressure_keys if isinstance(initial[key], (int, float))):
                issues.append(f"{node.get('id')}: 초기 절대압은 0보다 커야 함")
            temperature_keys = [key for key in initial if "temperature_K" in str(key)]
            if any(float(initial[key]) <= 0 for key in temperature_keys if isinstance(initial[key], (int, float))):
                issues.append(f"{node.get('id')}: 초기 절대온도는 0보다 커야 함")
            for fraction_key in ("liquid_fill_fraction", "opening_fraction", "load_fraction", "vapor_quality"):
                if fraction_key in initial and isinstance(initial[fraction_key], (int, float)):
                    if not 0.0 <= float(initial[fraction_key]) <= 1.0:
                        issues.append(f"{node.get('id')}: {fraction_key}는 0~1 범위여야 함")
        model = node.get("component_model")
        module_id = node.get("module_id")
        if module_id:
            auxiliary_modules += 1
            try:
                _validate_auxiliary_module(str(module_id), node.get("parameters", {}))
                ready_modules += 1
            except (TypeError, ValueError, KeyError) as exc:
                issues.append(f"{node.get('id')}: auxiliary module rejected parameters: {exc}")
            continue
        if not model:
            warnings.append(f"{node.get('id')}: 경계/헤더 노드(수치 부품 없음)")
            continue
        model_components += 1
        result = build_asset_model({
            "asset_id": str(node.get("id", "")),
            "component_model": str(model),
            "model_parameters": node.get("parameters", {}),
            "source": "authoring-ui explicit user design",
        })
        if result.ready:
            ready_components += 1
        else:
            issues.append(
                f"{node.get('id')}: " + "; ".join((*result.missing_parameters, *result.issues))
            )
    edge_ids: set[str] = set()
    for edge in edges:
        edge_id = str(edge.get("id", ""))
        if not edge_id or edge_id in edge_ids:
            issues.append(f"간선 ID가 없거나 중복됨: {edge_id or '(빈 값)'}")
        edge_ids.add(edge_id)
        if edge.get("source") not in known or edge.get("target") not in known:
            issues.append(f"{edge_id}: 존재하지 않는 끝점")
        if edge.get("source") == edge.get("target"):
            issues.append(f"{edge_id}: 자기 연결은 허용되지 않음")
    isolated = sorted(known - {
        str(value)
        for edge in edges
        for value in (edge.get("source"), edge.get("target"))
    })
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "physical_component_count": model_components,
        "ready_component_count": ready_components,
        "auxiliary_module_count": auxiliary_modules,
        "ready_auxiliary_module_count": ready_modules,
        "computational_block_count": model_components + auxiliary_modules,
        "ready_computational_block_count": ready_components + ready_modules,
        "isolated_node_ids": isolated,
        "issues": issues,
        "warnings": warnings,
        "numerically_buildable": (
            not issues
            and model_components == ready_components
            and auxiliary_modules == ready_modules
        ),
        "facility_binding_ready": False,
        "screening_parameters": True,
    }


def run_design_preview(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Run edited homogeneous tank instances as a physical heat-leak preview."""

    design = payload.get("design", payload)
    conditions = dict(design.get("process_conditions", {}))
    for key in ("duration_s", "time_step_s", "ambient_temperature_K"):
        if key in payload:
            conditions[key] = payload[key]
    validate_process_conditions(conditions)
    duration_s = float(conditions.get("duration_s", 1.0))
    time_step_s = float(conditions.get("time_step_s", 0.1))
    ambient_temperature_K = float(conditions.get("ambient_temperature_K", 300.0))
    initial_pressure_Pa = float(payload.get("initial_pressure_Pa", 400_000.0))
    liquid_fill_fraction = float(payload.get("liquid_fill_fraction", 0.5))
    if not (0 < duration_s <= 86_400 and 0 < time_step_s <= duration_s):
        raise ValueError("duration_s/time_step_s is outside the preview range")
    if not (0.01 <= liquid_fill_fraction <= 0.99):
        raise ValueError("liquid_fill_fraction must be between 0.01 and 0.99")
    series: dict[str, list[dict[str, float]]] = {}
    summaries: dict[str, dict[str, float]] = {}
    for node in design.get("nodes", []):
        if node.get("component_model") != "homogeneous_tank":
            continue
        p = node.get("parameters", {})
        initial_conditions = node.get("initial_conditions", {})
        geometry = TankGeometry(
            volume_m3=float(p["volume_m3"]),
            wall_heat_capacity_J_K=float(p["wall_heat_capacity_J_K"]),
            ambient_UA_W_K=float(p["ambient_UA_W_K"]),
            fluid_wall_UA_W_K=float(p["fluid_wall_UA_W_K"]),
        )
        tank = HomogeneousTank(geometry)
        initial = tank.initialize_saturated(
            float(initial_conditions.get("pressure_Pa_abs", initial_pressure_Pa)),
            float(initial_conditions.get("liquid_fill_fraction", liquid_fill_fraction)),
            wall_temperature_K=float(initial_conditions.get(
                "wall_temperature_K",
                tank.properties.saturated_liquid(
                    float(initial_conditions.get("pressure_Pa_abs", initial_pressure_Pa))
                ).temperature_K,
            )),
        )
        steps = tank.simulate(
            initial,
            duration_s=duration_s,
            time_step_s=time_step_s,
            ambient_temperature_K=ambient_temperature_K,
            flow_callback=lambda _t, _s: (),
        )
        records = [{
            "time_s": step.time_s,
            "pressure_Pa": step.thermo.pressure_Pa,
            "temperature_K": step.thermo.temperature_K,
            "mass_kg": step.state.hydrogen_mass_kg,
            "wall_temperature_K": step.state.wall_temperature_K,
            "energy_residual_J": step.total_energy_residual_J,
        } for step in steps]
        node_id = str(node["id"])
        series[node_id] = records
        summaries[node_id] = {
            "initial_pressure_Pa": records[0]["pressure_Pa"],
            "final_pressure_Pa": records[-1]["pressure_Pa"],
            "pressure_change_Pa": records[-1]["pressure_Pa"] - records[0]["pressure_Pa"],
            "final_mass_kg": records[-1]["mass_kg"],
            "max_abs_energy_residual_J": max(abs(item["energy_residual_J"]) for item in records),
            "applied_initial_conditions": dict(initial_conditions),
        }
    if not series:
        raise ValueError("설계에 homogeneous_tank 물리 부품이 없습니다")
    return {
        "mode": "edited_design_heat_leak_preview",
        "model_family": "HomogeneousTank",
        "duration_s": duration_s,
        "time_step_s": time_step_s,
        "ambient_temperature_K": ambient_temperature_K,
        "screening_parameters": True,
        "fitting_or_inverse_estimation": False,
        "facility_binding_ready": False,
        "series": series,
        "summaries": summaries,
    }


def run_public_scenario(scenario_id: str, design: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if scenario_id not in SCENARIO_MODULES:
        raise ValueError(f'Unknown public example: {scenario_id}')
    design = design if design is not None else build_bootstrap()['design']
    conditions = dict(design.get('process_conditions', {}))
    validate_process_conditions(conditions)
    nodes = {n['id']:n for n in design.get('nodes',[])}
    if 'DEMO-TANK' not in nodes:
        raise ValueError('This fixed demonstration requires DEMO-TANK. Use edited-design preview for other tanks.')
    node = nodes['DEMO-TANK']
    if node.get('component_model') != 'homogeneous_tank':
        raise ValueError('DEMO-TANK must use the homogeneous_tank model')
    p = node['parameters']; initial = node.get('initial_conditions',{})
    if not initial.get('enabled',True):
        raise ValueError('Enable DEMO-TANK before running')
    geometry = TankGeometry(**{key:float(p[key]) for key in ('volume_m3','wall_heat_capacity_J_K','ambient_UA_W_K','fluid_wall_UA_W_K')})
    tank = HomogeneousTank(geometry)
    pressure = float(initial.get('pressure_Pa_abs',400000.0))
    state = tank.initialize_saturated(pressure, float(initial.get('liquid_fill_fraction',0.5)), wall_temperature_K=float(initial.get('wall_temperature_K',26.0)))
    duration = float(conditions.get('duration_s',2.0)); dt = float(conditions.get('time_step_s',0.2))
    ambient = float(conditions.get('ambient_temperature_K',295.0))
    mass_flow = 0.0
    edge_id = None
    if scenario_id != 'DEMO_STORAGE':
        boundary_id,edge_id = ('DEMO-SOURCE','DEMO-LINE-IN') if scenario_id == 'DEMO_FILL' else ('DEMO-OUTLET','DEMO-LINE-OUT')
        if boundary_id not in nodes:
            raise ValueError(f'This fixed example requires {boundary_id}')
        edge = next((e for e in design.get('edges',[]) if e['id']==edge_id),None)
        expected = ('DEMO-SOURCE','DEMO-TANK') if scenario_id=='DEMO_FILL' else ('DEMO-TANK','DEMO-OUTLET')
        if edge is None or (edge['source'],edge['target']) != expected or not edge.get('enabled',True):
            raise ValueError('Restore the required demonstration connection, or use edited-design preview')
        boundary = nodes[boundary_id].get('initial_conditions',{})
        if not boundary.get('enabled',True): raise ValueError('Enable the selected boundary')
        mass_flow = float(boundary.get('mass_flow_kg_s',0.001))
        if not math.isfinite(mass_flow) or not 0 <= mass_flow <= 0.01:
            raise ValueError('Public example boundary flow must be 0–0.01 kg/s')
    flow_sign = 1 if scenario_id=='DEMO_FILL' else -1
    inlet_h = tank.properties.saturated_liquid(pressure).specific_enthalpy_J_kg
    def flows(_time,thermo):
        if mass_flow==0: return ()
        h = inlet_h if flow_sign>0 else thermo.specific_enthalpy_J_kg
        return (MassEnergyFlow(flow_sign*mass_flow,h,source='synthetic boundary'),)
    # Tank outflow enthalpy is owned by the homogeneous tank model; no phase-selective discharge is claimed.
    time_s=0.0; records=[]; boundary_mass=0.0; residual=0.0
    initial_mass=state.hydrogen_mass_kg
    while time_s < duration - 1e-12:
        emit_step(None)
        interval=min(dt,duration-time_s)
        step=tank.simulate(state,duration_s=interval,time_step_s=interval,ambient_temperature_K=ambient,flow_callback=flows)[-1]
        state=step.state;time_s+=interval;boundary_mass+=step.cumulative_boundary_mass_kg;residual+=step.total_energy_residual_J
        row={'time_s':time_s,'source':'solved','pressure_Pa':step.thermo.pressure_Pa,'temperature_K':step.thermo.temperature_K,'mass_kg':state.hydrogen_mass_kg,'wall_temperature_K':state.wall_temperature_K,'mass_flow_kg_s':mass_flow,'energy_residual_J':residual,'mass_residual_kg':state.hydrogen_mass_kg-initial_mass-boundary_mass}
        records.append(row)
        telemetry={'node:DEMO-TANK':row}
        if edge_id:telemetry['edge:'+edge_id]={'time_s':time_s,'source':'prescribed_boundary','mass_flow_kg_s':mass_flow}
        emit_step(telemetry)
    return {'scenario_id':scenario_id,'mode':'synthetic_prescribed_boundary','ui_execution_mode':'public_synthetic_example','model_family':'HomogeneousTank','duration_s':duration,'time_step_s':dt,'series':{'DEMO-TANK':records},'summaries':{'DEMO-TANK':{'final_pressure_Pa':records[-1]['pressure_Pa'],'final_mass_kg':state.hydrogen_mass_kg,'boundary_mass_kg':boundary_mass,'mass_residual_kg':records[-1]['mass_residual_kg'],'energy_residual_J':residual}},'screening_parameters':True,'fitting_or_inverse_estimation':False,'facility_binding_ready':False,'ui_active_edges':SCENARIO_EDGES[scenario_id],'ui_process_conditions':conditions,'ui_initial_conditions_applied':True}


def validate_process_conditions(values):
    duration = float(values.get("duration_s", 1.0))
    step = float(values.get("time_step_s", 0.1))
    ambient = float(values.get("ambient_temperature_K", 300.0))
    if not all(math.isfinite(v) for v in (duration, step, ambient)):
        raise ValueError("운전조건은 유한한 숫자여야 합니다")
    if not (1e-6 <= duration <= 86400 and 1e-6 <= step <= duration and ambient >= 1e-6):
        raise ValueError("계산시간 0~86400초, 시간간격 0~계산시간, 외기온도 >0 K를 입력하세요")
    if math.ceil(duration / step) > 10000:
        raise ValueError("최대 10000단계입니다. 시간간격을 늘려주세요")


def _thermo_values(thermo):
    names = ("pressure_Pa", "temperature_K", "density_kg_m3",
             "specific_enthalpy_J_kg", "specific_internal_energy_J_kg", "specific_entropy_J_kgK")
    values = {key: getattr(thermo, key) for key in names if getattr(thermo, key, None) is not None}
    if getattr(thermo, "phase", None) is not None: values["phase"] = thermo.phase
    if getattr(thermo, "quality", None) is not None:
        values["vapor_quality"] = thermo.quality
    return values


def _line_values(left, right, flow, time_s):
    upstream, downstream = (left, right) if flow >= 0 else (right, left)
    row = {"time_s": time_s, "source": "solved", **_thermo_values(upstream), "mass_flow_kg_s": flow,
           "inlet_pressure_Pa": left.pressure_Pa, "inlet_temperature_K": left.temperature_K,
           "outlet_pressure_Pa": right.pressure_Pa, "outlet_temperature_K": right.temperature_K,
           "downstream_pressure_Pa": downstream.pressure_Pa, "downstream_temperature_K": downstream.temperature_K,
           "pressure_drop_Pa": left.pressure_Pa - right.pressure_Pa,
           "temperature_change_K": right.temperature_K - left.temperature_K}
    density = getattr(upstream, "density_kg_m3", None)
    if density is not None and density > 0:
        row["volumetric_flow_m3_s"] = flow / density
    return row


def step_telemetry(step):
    return step if isinstance(step, dict) else {}


class SimulationCancelled(Exception):
    pass


def initial_monitor_rows(design):
    """Input references for every UI target, explicitly distinct from solved states."""
    rows = {}
    for node in design.get("nodes", []):
        initial = node.get("initial_conditions", {})
        row = {"time_s": 0.0, "source": "initial_input"}
        for target, candidates in {
            "pressure_Pa": ("pressure_Pa_abs", "inlet_pressure_Pa_abs"),
            "temperature_K": ("temperature_K", "fluid_temperature_K", "inlet_temperature_K"),
            "wall_temperature_K": ("wall_temperature_K",),
            "lower_wall_temperature_K": ("lower_wall_temperature_K",),
            "upper_wall_temperature_K": ("upper_wall_temperature_K",),
            "liquid_fill_fraction": ("liquid_fill_fraction",),
            "speed_rpm": ("speed_rpm",), "opening_fraction": ("opening_fraction",),
            "load_fraction": ("load_fraction",), "initial_input": ("initial_input",),
            "initial_output": ("initial_output",), "setpoint": ("setpoint",),
        }.items():
            for candidate in candidates:
                if candidate in initial:
                    row[target] = initial[candidate]
                    break
        rows["node:" + node["id"]] = row
    for edge in design.get("edges", []):
        start, end = rows.get("node:" + edge["source"], {}), rows.get("node:" + edge["target"], {})
        row = {"time_s": 0.0, "source": "endpoint_inputs"}
        for key in ("pressure_Pa", "temperature_K"):
            if key in start: row[key] = row["inlet_" + key] = start[key]
            if key in end: row["outlet_" + key] = end[key]
        # A topology edge alone cannot determine flow or internal stagnant state.
        rows["edge:" + edge["id"]] = row
    return rows


class SimulationJob:
    def __init__(self, payload):
        self.id = uuid.uuid4().hex
        self.payload = payload
        self.status = "running"
        self.series = {}
        self.result = None
        self.error = None
        self.initial_rows = initial_monitor_rows(payload.get("design", {}))
        self.gate = threading.Event()
        self.gate.set()
        self.cancelled = threading.Event()
        self.lock = threading.RLock()

    def observe(self, step):
        if self.cancelled.is_set():
            raise SimulationCancelled()
        if not self.gate.is_set():
            with self.lock:
                self.status = "paused"
            self.gate.wait()
        if self.cancelled.is_set():
            raise SimulationCancelled()
        if step is not None:
            with self.lock:
                for key, row in step_telemetry(step).items():
                    self.series.setdefault(key, []).append(row)

    def run(self):
        try:
            with observe_steps(self.observe):
                result = run_public_scenario(self.payload["scenario_id"], self.payload.get("design"))
            self.observe(None)
            with self.lock:
                self.result = result
                self.status = "completed"
        except SimulationCancelled:
            with self.lock:
                self.status = "cancelled"
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
                self.status = "failed"

    def snapshot(self):
        with self.lock:
            return {"job_id": self.id, "status": self.status, "series": {k:list(v) for k,v in self.series.items()},
                    "initial_rows": self.initial_rows,
                    "result": self.result, "error": self.error}

JOBS = {}
JOBS_LOCK = threading.Lock()


def _safe_design_name(value: str) -> str:
    name = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", value.strip()).strip("_")
    return name[:80] or "lh2_design"


class AuthoringHandler(SimpleHTTPRequestHandler):
    server_version = "LH2Authoring/0.1"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 10_000_000:
            raise ValueError("request body is too large")
        value = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        if not isinstance(value, dict):
            raise ValueError("request root must be an object")
        return value

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/jobs/"):
                with JOBS_LOCK:
                    job = JOBS[parsed.path.rsplit("/", 1)[-1]]
                self._send_json(job.snapshot())
                return
            if parsed.path == "/api/bootstrap":
                self._send_json(build_bootstrap())
                return
            if parsed.path == "/api/designs":
                DESIGN_DIR.mkdir(parents=True, exist_ok=True)
                self._send_json({"designs": [p.name for p in sorted(DESIGN_DIR.glob("*.json"))]})
                return
            if parsed.path.startswith("/api/designs/"):
                name = _safe_design_name(unquote(parsed.path.rsplit("/", 1)[-1]).removesuffix(".json"))
                self._send_json(_read_json(DESIGN_DIR / f"{name}.json"))
                return
            super().do_GET()
        except Exception as exc:  # UI boundary: return actionable error details.
            self._send_json({"error": str(exc), "type": type(exc).__name__}, 400)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            body = self._body()
            if parsed.path == "/api/jobs":
                validate_process_conditions(body.get("design", {}).get("process_conditions", {}))
                if body.get("scenario_id") not in SCENARIO_MODULES:
                    raise ValueError("알 수 없는 시나리오")
                with JOBS_LOCK:
                    if any(j.status in ("running", "pausing", "paused", "cancelling") for j in JOBS.values()):
                        raise ValueError("진행 중인 계산을 초기화하거나 완료한 후 실행하세요")
                    if len(JOBS) >= 20:
                        for key in list(JOBS):
                            if JOBS[key].status in ("completed", "failed", "cancelled"):
                                del JOBS[key]
                    job = SimulationJob(body)
                    JOBS[job.id] = job
                threading.Thread(target=job.run, daemon=True).start()
                self._send_json({"job_id": job.id})
                return
            if parsed.path.startswith("/api/jobs/"):
                with JOBS_LOCK:
                    job = JOBS[parsed.path.split("/")[3]]
                action = body.get("action")
                with job.lock:
                    if action == "cancel":
                        job.cancelled.set()
                        job.gate.set()
                        job.status = "cancelling" if job.status not in ("completed", "failed", "cancelled") else "cancelled"
                    elif action == "pause" and job.status == "running":
                        job.gate.clear()
                        job.status = "pausing"
                    elif action == "resume" and job.status in ("paused", "pausing"):
                        job.status = "running"
                        job.gate.set()
                self._send_json(job.snapshot())
                return
            if parsed.path == "/api/validate":
                self._send_json(validate_design(body.get("design", body)))
                return
            if parsed.path == "/api/simulate":
                mode = body.get("mode", "public_scenario")
                result = (
                    run_public_scenario(
                        str(body.get("scenario_id", "")),
                        body.get("design") if isinstance(body.get("design"), Mapping) else None,
                    )
                    if mode == "public_scenario"
                    else run_design_preview(body)
                )
                self._send_json(result)
                return
            if parsed.path == "/api/save":
                design = body.get("design", body)
                audit = validate_design(design)
                name = _safe_design_name(str(body.get("name") or design.get("design_id") or "lh2_design"))
                DESIGN_DIR.mkdir(parents=True, exist_ok=True)
                path = DESIGN_DIR / f"{name}.json"
                path.write_text(json.dumps(design, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                self._send_json({"saved": True, "name": path.name, "path": str(path), "audit": audit})
                return
            self._send_json({"error": "unknown API endpoint"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc), "type": type(exc).__name__}, 400)

    def log_message(self, format: str, *args: Any) -> None:
        if self.path.startswith("/api/jobs/") and self.command == "GET":
            return
        print("[LH2 authoring] " + format % args)


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), AuthoringHandler)
    print(f"LH2 물리모델 저작도구: http://{host}:{port}")
    print("종료: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LH2 digital-twin authoring UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.host, args.port)
