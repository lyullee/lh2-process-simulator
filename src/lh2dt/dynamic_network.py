"""Conservative coupling of dynamic storage models to a steady network.

The pressure/enthalpy network is solved at each Runge--Kutta stage.  Dynamic
nodes provide their thermodynamic boundary state and a first-principles
derivative; the network supplies signed boundary streams back to those nodes.
No plant time series are used to choose parameters or repair timestamps.
"""

from __future__ import annotations
from .simulation_observer import emit_step

from dataclasses import dataclass, field, replace
from bisect import bisect_right
import math
from typing import Callable, Generic, Iterable, Mapping, Sequence, TypeVar

from .network import LinkSolution, NetworkSolution, SteadyNetwork
from .properties import ThermoState
from .streams import MassEnergyFlow
from .tank import HomogeneousTank, TankDerivative, TankState
from .stratified_tank import (
    StratifiedTank,
    StratifiedTankDerivative,
    StratifiedTankState,
    WallHeatCallback,
)
from .dynamic_pipe import DynamicHEMPipe, DynamicHEMPipeDerivative, DynamicHEMPipeState
from .dynamic_vaporizer import (
    DynamicVaporizer,
    DynamicVaporizerDerivative,
    DynamicVaporizerState,
    ParallelDynamicVaporizer,
    ParallelDynamicVaporizerDerivative,
    ParallelDynamicVaporizerState,
)
from .dynamic_reliquefier import (
    DynamicReliquefier,
    ParallelDynamicReliquefier,
    ParallelDynamicReliquefierDerivative,
    ParallelDynamicReliquefierState,
)


StateT = TypeVar("StateT")
DerivativeT = TypeVar("DerivativeT")


class NetworkConvergenceError(RuntimeError):
    """A failed junction solve cannot supply transport to a dynamic stage.

    ``solution`` retains the optimizer, enthalpy and conservation verdicts,
    along with per-junction residuals and tolerances, for caller diagnostics.
    No storage derivative or accepted state update is computed for this stage.
    """

    def __init__(self, time_s: float, solution: NetworkSolution) -> None:
        self.time_s = float(time_s)
        self.solution = solution
        super().__init__(
            f"Network solve failed at t={self.time_s:.17g} s "
            f"(pressure_optimizer={solution.pressure_optimizer_success}, "
            f"enthalpy={solution.enthalpy_solver_success}, "
            f"junction_conservation={solution.junction_conservation_success})"
        )


@dataclass(frozen=True)
class DynamicNodeModel(Generic[StateT, DerivativeT]):
    """Adapter contract for any finite-storage component.

    ``derivative`` must implement the component's conservation equations.
    ``advance`` and ``combine_derivatives`` are supplied by the component,
    so a layered or distributed tank can be plugged in without changing the
    network solver.
    """

    name: str
    initial_state: StateT
    thermo: Callable[[StateT], ThermoState]
    derivative: Callable[[StateT, Iterable[MassEnergyFlow], float], DerivativeT]
    advance: Callable[[StateT, DerivativeT, float], StateT]
    combine_derivatives: Callable[[DerivativeT, DerivativeT, DerivativeT, DerivativeT], DerivativeT]
    boundary_thermo: Callable[[StateT], Mapping[str, ThermoState]] | None = None
    boundary_derivative: Callable[[StateT, Mapping[str, tuple[MassEnergyFlow, ...]], float], DerivativeT] | None = None
    representative_boundary: str | None = None
    boundary_heat: Callable[[StateT, tuple[MassEnergyFlow, ...], float, DerivativeT], float] | None = None

    def boundary_states_for(self, state: StateT, *, owner_name: str | None = None) -> dict[str, ThermoState]:
        """Return explicitly declared network ports of this one stored state.

        The single-boundary adapter remains the default. Multi-boundary
        adapters must supply both phase/port states and a derivative that
        consumes separately routed port flows. Aliases do not own inventories.
        """
        if (self.boundary_thermo is None) != (self.boundary_derivative is None):
            raise ValueError("boundary_thermo and boundary_derivative must be supplied together")
        if self.boundary_thermo is None:
            if self.representative_boundary is not None:
                raise ValueError("representative_boundary requires explicit boundary_thermo")
            return {self.name if owner_name is None else owner_name: self.thermo(state)}
        raw = self.boundary_thermo(state)
        if not isinstance(raw, Mapping) or not raw:
            raise ValueError("boundary_thermo must return a non-empty mapping")
        if any(not isinstance(name, str) or not name.strip() or name != name.strip() for name in raw):
            raise ValueError("boundary_thermo aliases must be explicit non-empty strings")
        if self.representative_boundary is not None and self.representative_boundary not in raw:
            raise ValueError("representative_boundary is not a declared network boundary")
        return dict(raw)


@dataclass(frozen=True)
class DynamicNetworkStepResult(Generic[StateT]):
    time_s: float
    states: dict[str, StateT]
    thermo_states: dict[str, ThermoState]
    network_solution: NetworkSolution
    node_flows: dict[str, tuple[MassEnergyFlow, ...]]
    node_mass_flow_kg_s: dict[str, float]
    node_transport_energy_W: dict[str, float]
    node_ambient_heat_W: dict[str, float]
    cumulative_boundary_mass_kg: dict[str, float]
    cumulative_boundary_energy_J: dict[str, float]
    cumulative_boundary_transport_energy_J: dict[str, float]
    cumulative_boundary_ambient_heat_J: dict[str, float]
    network_boundary_mass_flow_kg_s: float
    network_boundary_transport_energy_W: float
    network_boundary_ambient_heat_W: float
    cumulative_network_boundary_mass_kg: float
    cumulative_network_boundary_energy_J: float
    cumulative_network_boundary_transport_energy_J: float
    cumulative_network_boundary_ambient_heat_J: float
    port_thermo_states: dict[str, ThermoState] = field(default_factory=dict)
    link_potential_energy_change_W: float = 0.0
    cumulative_link_potential_energy_change_J: float = 0.0
    # Link transport diagnostics do not infer node heights or replace a
    # separately computed inventory of stored gravitational potential.

    @property
    def node_external_heat_W(self) -> dict[str, float]:
        """Non-transport boundary heat, including declared direct heat inputs."""
        return self.node_ambient_heat_W

    @property
    def cumulative_boundary_external_heat_J(self) -> dict[str, float]:
        return self.cumulative_boundary_ambient_heat_J


def homogeneous_tank_node(
    name: str,
    tank: HomogeneousTank,
    initial_state: TankState,
    ambient_temperature_K: float | Callable[[float], float],
) -> DynamicNodeModel[TankState, TankDerivative]:
    """Create the standard dynamic adapter for ``HomogeneousTank``."""

    def ambient(_time_s: float) -> float:
        value = ambient_temperature_K(_time_s) if callable(ambient_temperature_K) else ambient_temperature_K
        return float(value)

    def derivative(state: TankState, flows: Iterable[MassEnergyFlow], time_s: float) -> TankDerivative:
        return tank.derivative(state, flows, ambient(time_s))

    def combine(
        k1: TankDerivative,
        k2: TankDerivative,
        k3: TankDerivative,
        k4: TankDerivative,
    ) -> TankDerivative:
        return TankDerivative(
            mass_kg_s=(k1.mass_kg_s + 2.0 * k2.mass_kg_s + 2.0 * k3.mass_kg_s + k4.mass_kg_s) / 6.0,
            internal_energy_W=(k1.internal_energy_W + 2.0 * k2.internal_energy_W + 2.0 * k3.internal_energy_W + k4.internal_energy_W) / 6.0,
            wall_temperature_K_s=(k1.wall_temperature_K_s + 2.0 * k2.wall_temperature_K_s + 2.0 * k3.wall_temperature_K_s + k4.wall_temperature_K_s) / 6.0,
            ambient_heat_W=(k1.ambient_heat_W + 2.0 * k2.ambient_heat_W + 2.0 * k3.ambient_heat_W + k4.ambient_heat_W) / 6.0,
            wall_to_fluid_heat_W=(k1.wall_to_fluid_heat_W + 2.0 * k2.wall_to_fluid_heat_W + 2.0 * k3.wall_to_fluid_heat_W + k4.wall_to_fluid_heat_W) / 6.0,
        )

    return DynamicNodeModel(
        name=name,
        initial_state=initial_state,
        thermo=tank.thermo,
        derivative=derivative,
        advance=tank._advance,
        combine_derivatives=combine,
    )


def stratified_tank_node(
    name: str,
    tank: StratifiedTank,
    initial_state: StratifiedTankState,
    boundary_phase: str,
    ambient_temperature_K: float | Callable[[float], float],
    wall_heat_callback: WallHeatCallback | None = None,
) -> DynamicNodeModel[StratifiedTankState, StratifiedTankDerivative]:
    """Adapt one explicit stratified-tank phase port to the dynamic network.

    The generic dynamic network exposes one thermodynamic boundary state per
    dynamic node.  A stratified tank has separate liquid and vapor inventories,
    so the caller must explicitly choose which phase the selected network port
    represents.  All node flows are assigned to that phase; the other phase
    remains internal to the tank model.  No phase or initial state is inferred.
    """

    phase = str(boundary_phase).strip().lower()
    if phase not in {"liquid", "vapor"}:
        raise ValueError("boundary_phase must be 'liquid' or 'vapor'")

    def ambient(_time_s: float) -> float:
        value = (
            ambient_temperature_K(_time_s)
            if callable(ambient_temperature_K)
            else ambient_temperature_K
        )
        return float(value)

    def boundary_thermo(state: StratifiedTankState) -> ThermoState:
        thermo = tank.thermo(state)
        return thermo.liquid if phase == "liquid" else thermo.vapor

    def derivative(
        state: StratifiedTankState,
        flows: Iterable[MassEnergyFlow],
        time_s: float,
    ) -> StratifiedTankDerivative:
        thermo = tank.thermo(state)
        direct_lower, direct_upper = (
            wall_heat_callback(time_s, thermo)
            if wall_heat_callback is not None
            else (0.0, 0.0)
        )
        if phase == "liquid":
            return tank.derivative(
                state,
                flows,
                (),
                ambient(time_s),
                direct_lower_wall_heat_W=direct_lower,
                direct_upper_wall_heat_W=direct_upper,
                pressure_hint_Pa=thermo.pressure_Pa,
            )
        return tank.derivative(
            state,
            (),
            flows,
            ambient(time_s),
            direct_lower_wall_heat_W=direct_lower,
            direct_upper_wall_heat_W=direct_upper,
            pressure_hint_Pa=thermo.pressure_Pa,
        )

    def boundary_heat(
        state: StratifiedTankState,
        _flows: tuple[MassEnergyFlow, ...],
        time_s: float,
        derivative_value: StratifiedTankDerivative,
    ) -> float:
        if wall_heat_callback is None:
            return derivative_value.ambient_heat_W
        thermo = tank.thermo(state)
        lower, upper = wall_heat_callback(time_s, thermo)
        return derivative_value.ambient_heat_W + float(lower) + float(upper)

    def combine(
        k1: StratifiedTankDerivative,
        k2: StratifiedTankDerivative,
        k3: StratifiedTankDerivative,
        k4: StratifiedTankDerivative,
    ) -> StratifiedTankDerivative:
        def average(name: str) -> float:
            return (
                getattr(k1, name)
                + 2.0 * getattr(k2, name)
                + 2.0 * getattr(k3, name)
                + getattr(k4, name)
            ) / 6.0

        return StratifiedTankDerivative(
            liquid_mass_kg_s=average("liquid_mass_kg_s"),
            liquid_internal_energy_W=average("liquid_internal_energy_W"),
            vapor_mass_kg_s=average("vapor_mass_kg_s"),
            vapor_internal_energy_W=average("vapor_internal_energy_W"),
            lower_wall_temperature_K_s=average("lower_wall_temperature_K_s"),
            upper_wall_temperature_K_s=average("upper_wall_temperature_K_s"),
            evaporation_rate_kg_s=average("evaporation_rate_kg_s"),
            lower_ambient_heat_W=average("lower_ambient_heat_W"),
            upper_ambient_heat_W=average("upper_ambient_heat_W"),
            lower_wall_to_liquid_heat_W=average("lower_wall_to_liquid_heat_W"),
            lower_wall_to_vapor_heat_W=average("lower_wall_to_vapor_heat_W"),
            upper_wall_to_liquid_heat_W=average("upper_wall_to_liquid_heat_W"),
            upper_wall_to_vapor_heat_W=average("upper_wall_to_vapor_heat_W"),
            liquid_to_interface_heat_W=average("liquid_to_interface_heat_W"),
            vapor_to_interface_heat_W=average("vapor_to_interface_heat_W"),
            lower_to_upper_wall_heat_W=average("lower_to_upper_wall_heat_W"),
        )

    return DynamicNodeModel(
        name=name,
        initial_state=initial_state,
        thermo=boundary_thermo,
        derivative=derivative,
        advance=tank._advance,
        combine_derivatives=combine,
        boundary_heat=boundary_heat,
    )


def stratified_tank_ports_node(
    name: str,
    tank: StratifiedTank,
    initial_state: StratifiedTankState,
    *,
    liquid_boundary_node: str,
    vapor_boundary_node: str,
    representative_phase: str,
    ambient_temperature_K: float | Callable[[float], float],
    wall_heat_callback: WallHeatCallback | None = None,
) -> DynamicNodeModel[StratifiedTankState, StratifiedTankDerivative]:
    """Expose liquid and vapor ports sharing one physical tank inventory.

    Boundary aliases and phase ownership are caller declarations. The
    physical owner name stays in ``result.states`` exactly once. Phase-specific
    outlet enthalpies are published in ``result.port_thermo_states``. The
    representative phase selects only the compatibility ``thermo_states``
    view; it does not assign all flows to that phase.
    """
    aliases = (name, liquid_boundary_node, vapor_boundary_node)
    if any(not isinstance(alias, str) or not alias.strip() or alias != alias.strip() for alias in aliases):
        raise ValueError("tank owner and boundary aliases must be explicit non-empty strings")
    if len(set(aliases)) != 3:
        raise ValueError("tank owner, liquid and vapor boundary aliases must be distinct")
    if representative_phase not in {"liquid", "vapor"}:
        raise ValueError("representative_phase must be explicitly liquid or vapor")
    base = stratified_tank_node(
        name, tank, initial_state, representative_phase, ambient_temperature_K,
        wall_heat_callback=wall_heat_callback,
    )

    def boundary_thermo(state):
        thermo = tank.thermo(state)
        return {liquid_boundary_node: thermo.liquid, vapor_boundary_node: thermo.vapor}

    def boundary_derivative(state, flows_by_boundary, time_s):
        if set(flows_by_boundary) != {liquid_boundary_node, vapor_boundary_node}:
            raise ValueError("stratified tank requires both explicitly routed phase boundary streams")
        ambient = ambient_temperature_K(time_s) if callable(ambient_temperature_K) else ambient_temperature_K
        direct_lower, direct_upper = (
            wall_heat_callback(time_s, tank.thermo(state))
            if wall_heat_callback is not None
            else (0.0, 0.0)
        )
        return tank.derivative(state, flows_by_boundary[liquid_boundary_node],
                               flows_by_boundary[vapor_boundary_node], float(ambient),
                               direct_lower_wall_heat_W=direct_lower,
                               direct_upper_wall_heat_W=direct_upper,
                               pressure_hint_Pa=tank.thermo(state).pressure_Pa)

    return replace(base, boundary_thermo=boundary_thermo, boundary_derivative=boundary_derivative,
                   representative_boundary=liquid_boundary_node if representative_phase == "liquid" else vapor_boundary_node)


def dynamic_hem_pipe_node(
    name: str,
    pipe: DynamicHEMPipe,
    initial_state: DynamicHEMPipeState,
) -> DynamicNodeModel[DynamicHEMPipeState, DynamicHEMPipeDerivative]:
    """Adapt a finite-volume HEM pipe to the reusable dynamic network.

    The caller attaches ``pipe.inlet_hydraulic_component`` and
    ``pipe.outlet_hydraulic_component`` as explicit links on the named
    boundary node.  No pressure, enthalpy, or flow value is inferred by this
    adapter; the steady network supplies signed streams and the pipe applies
    its own mass, fluid-energy, and wall-energy equations.
    """

    def derivative(
        state: DynamicHEMPipeState,
        flows: Iterable[MassEnergyFlow],
        _time_s: float,
    ) -> DynamicHEMPipeDerivative:
        return pipe.derivative_from_flows(state, flows)

    def combine(
        k1: DynamicHEMPipeDerivative,
        k2: DynamicHEMPipeDerivative,
        k3: DynamicHEMPipeDerivative,
        k4: DynamicHEMPipeDerivative,
    ) -> DynamicHEMPipeDerivative:
        return DynamicHEMPipeDerivative(
            mass_kg_s=(k1.mass_kg_s + 2.0 * k2.mass_kg_s + 2.0 * k3.mass_kg_s + k4.mass_kg_s) / 6.0,
            internal_energy_W=(k1.internal_energy_W + 2.0 * k2.internal_energy_W + 2.0 * k3.internal_energy_W + k4.internal_energy_W) / 6.0,
            wall_temperature_K_s=(k1.wall_temperature_K_s + 2.0 * k2.wall_temperature_K_s + 2.0 * k3.wall_temperature_K_s + k4.wall_temperature_K_s) / 6.0,
            mass_flow_in_kg_s=(k1.mass_flow_in_kg_s + 2.0 * k2.mass_flow_in_kg_s + 2.0 * k3.mass_flow_in_kg_s + k4.mass_flow_in_kg_s) / 6.0,
            mass_flow_out_kg_s=(k1.mass_flow_out_kg_s + 2.0 * k2.mass_flow_out_kg_s + 2.0 * k3.mass_flow_out_kg_s + k4.mass_flow_out_kg_s) / 6.0,
            inlet_enthalpy_flow_W=(k1.inlet_enthalpy_flow_W + 2.0 * k2.inlet_enthalpy_flow_W + 2.0 * k3.inlet_enthalpy_flow_W + k4.inlet_enthalpy_flow_W) / 6.0,
            outlet_enthalpy_flow_W=(k1.outlet_enthalpy_flow_W + 2.0 * k2.outlet_enthalpy_flow_W + 2.0 * k3.outlet_enthalpy_flow_W + k4.outlet_enthalpy_flow_W) / 6.0,
            heat_from_wall_W=(k1.heat_from_wall_W + 2.0 * k2.heat_from_wall_W + 2.0 * k3.heat_from_wall_W + k4.heat_from_wall_W) / 6.0,
            heat_from_ambient_W=(k1.heat_from_ambient_W + 2.0 * k2.heat_from_ambient_W + 2.0 * k3.heat_from_ambient_W + k4.heat_from_ambient_W) / 6.0,
        )

    return DynamicNodeModel(
        name=name,
        initial_state=initial_state,
        thermo=pipe.thermo,
        derivative=derivative,
        advance=pipe._advance,
        combine_derivatives=combine,
    )


def dynamic_vaporizer_node(
    name: str,
    vaporizer: DynamicVaporizer,
    initial_state: DynamicVaporizerState,
    ambient_temperature_K: float | Callable[[float], float] | None = None,
) -> DynamicNodeModel[DynamicVaporizerState, DynamicVaporizerDerivative]:
    """Adapt a finite-volume vaporizer to the reusable dynamic network.

    The adapter only transports signed boundary streams into the vaporizer's
    mass/internal-energy/wall balance.  Inlet and outlet pressure losses stay
    in caller-selected hydraulic links, so a steady `Vaporizer` or this
    transient model can occupy the same explicit network boundary contract.
    ``ambient_temperature_K`` is an optional time-varying boundary condition;
    when omitted, the vaporizer's declared ambient temperature is used.
    """

    if ambient_temperature_K is None:
        def ambient(_time_s: float) -> float | None:
            return None
    elif callable(ambient_temperature_K):
        def ambient(time_s: float) -> float:
            return float(ambient_temperature_K(time_s))
    else:
        value = float(ambient_temperature_K)

        def ambient(_time_s: float) -> float:
            return value

    def derivative(
        state: DynamicVaporizerState,
        flows: Iterable[MassEnergyFlow],
        time_s: float,
    ) -> DynamicVaporizerDerivative:
        ambient_temperature = ambient(time_s)
        if ambient_temperature is None:
            return vaporizer.derivative_from_flows(state, flows)
        return vaporizer.derivative_from_flows(
            state,
            flows,
            ambient_temperature_K=ambient_temperature,
        )

    def combine(
        k1: DynamicVaporizerDerivative,
        k2: DynamicVaporizerDerivative,
        k3: DynamicVaporizerDerivative,
        k4: DynamicVaporizerDerivative,
    ) -> DynamicVaporizerDerivative:
        return DynamicVaporizerDerivative(
            mass_kg_s=(k1.mass_kg_s + 2.0 * k2.mass_kg_s + 2.0 * k3.mass_kg_s + k4.mass_kg_s) / 6.0,
            internal_energy_W=(k1.internal_energy_W + 2.0 * k2.internal_energy_W + 2.0 * k3.internal_energy_W + k4.internal_energy_W) / 6.0,
            wall_temperature_K_s=(k1.wall_temperature_K_s + 2.0 * k2.wall_temperature_K_s + 2.0 * k3.wall_temperature_K_s + k4.wall_temperature_K_s) / 6.0,
            mass_flow_in_kg_s=(k1.mass_flow_in_kg_s + 2.0 * k2.mass_flow_in_kg_s + 2.0 * k3.mass_flow_in_kg_s + k4.mass_flow_in_kg_s) / 6.0,
            mass_flow_out_kg_s=(k1.mass_flow_out_kg_s + 2.0 * k2.mass_flow_out_kg_s + 2.0 * k3.mass_flow_out_kg_s + k4.mass_flow_out_kg_s) / 6.0,
            inlet_enthalpy_flow_W=(k1.inlet_enthalpy_flow_W + 2.0 * k2.inlet_enthalpy_flow_W + 2.0 * k3.inlet_enthalpy_flow_W + k4.inlet_enthalpy_flow_W) / 6.0,
            outlet_enthalpy_flow_W=(k1.outlet_enthalpy_flow_W + 2.0 * k2.outlet_enthalpy_flow_W + 2.0 * k3.outlet_enthalpy_flow_W + k4.outlet_enthalpy_flow_W) / 6.0,
            heat_from_wall_W=(k1.heat_from_wall_W + 2.0 * k2.heat_from_wall_W + 2.0 * k3.heat_from_wall_W + k4.heat_from_wall_W) / 6.0,
            heat_from_ambient_W=(k1.heat_from_ambient_W + 2.0 * k2.heat_from_ambient_W + 2.0 * k3.heat_from_ambient_W + k4.heat_from_ambient_W) / 6.0,
        )

    return DynamicNodeModel(
        name=name,
        initial_state=initial_state,
        thermo=vaporizer.thermo,
        derivative=derivative,
        advance=vaporizer._advance,
        combine_derivatives=combine,
    )


def parallel_dynamic_vaporizer_node(
    name: str,
    vaporizer_bank: ParallelDynamicVaporizer,
    initial_state: ParallelDynamicVaporizerState,
) -> DynamicNodeModel[ParallelDynamicVaporizerState, ParallelDynamicVaporizerDerivative]:
    """Adapt an explicit multi-unit dynamic vaporizer bank.

    The bank remains one coarse network boundary while each vaporizer keeps
    its own conserved state.  Unit activation and flow weights are declared
    on the bank; no operating split is inferred by this adapter.
    """

    if not isinstance(vaporizer_bank, ParallelDynamicVaporizer):
        raise TypeError("vaporizer_bank must be a ParallelDynamicVaporizer")
    return DynamicNodeModel(
        name=name,
        initial_state=initial_state,
        thermo=vaporizer_bank.thermo,
        derivative=lambda state, flows, _time_s: vaporizer_bank.derivative_from_flows(state, flows),
        advance=vaporizer_bank.advance,
        combine_derivatives=vaporizer_bank.combine_derivatives,
    )


def dynamic_reliquefier_node(
    name: str,
    reliquefier: DynamicReliquefier,
    initial_state: DynamicVaporizerState,
) -> DynamicNodeModel[DynamicVaporizerState, DynamicVaporizerDerivative]:
    """Adapt a transient reliquefier to the common dynamic-node contract.

    The cold-side temperature and UA are declared on ``reliquefier`` and are
    therefore not silently replaced by a generic ambient boundary.  Hydraulic
    inlet/outlet links remain separate, allowing the steady ``ReliquefierLink``
    or a pair of explicit network links to be selected by the caller.
    """

    if not isinstance(reliquefier, DynamicReliquefier):
        raise TypeError("reliquefier must be a DynamicReliquefier")
    return dynamic_vaporizer_node(
        name,
        reliquefier,
        initial_state,
        ambient_temperature_K=reliquefier.cold_side_temperature,
    )


def parallel_dynamic_reliquefier_node(
    name: str,
    reliquefier_bank: ParallelDynamicReliquefier,
    initial_state: ParallelDynamicReliquefierState,
) -> DynamicNodeModel[ParallelDynamicReliquefierState, ParallelDynamicReliquefierDerivative]:
    """Adapt an explicit multi-unit dynamic reliquefier bank.

    The bank remains one coarse network boundary while each unit keeps its
    own conserved state.  Unit activation and flow weights are configured on
    the bank; no operating split is inferred by this adapter.
    """

    if not isinstance(reliquefier_bank, ParallelDynamicReliquefier):
        raise TypeError("reliquefier_bank must be a ParallelDynamicReliquefier")
    return DynamicNodeModel(
        name=name,
        initial_state=initial_state,
        thermo=reliquefier_bank.thermo,
        derivative=lambda state, flows, _time_s: reliquefier_bank.derivative_from_flows(state, flows),
        advance=reliquefier_bank.advance,
        combine_derivatives=reliquefier_bank.combine_derivatives,
    )


class DynamicNetworkSimulator:
    """Advance dynamic boundary nodes while solving a reusable steady network."""

    def __init__(
        self,
        network: SteadyNetwork,
        dynamic_nodes: Mapping[str, DynamicNodeModel[object, object]],
        command_callback: Callable[[float, SteadyNetwork], None] | None = None,
        pressure_solver_max_nfev: int = 300,
        *,
        command_event_times_s: Sequence[float] | None = None,
    ) -> None:
        if not dynamic_nodes:
            raise ValueError("At least one dynamic node is required")
        self.network = network
        self.dynamic_nodes = dict(dynamic_nodes)
        self.command_callback = command_callback
        # Bound ScenarioCommandSchedule callbacks expose their original
        # timestamps. Custom callbacks can supply the same explicit contract.
        # These are switching times, not observation times or inferred events.
        callback_events = getattr(command_callback, "event_times_s", ())
        supplied_events = () if command_event_times_s is None else command_event_times_s
        validated_events = []
        for label, events in (("callback event_times_s", callback_events),
                              ("command_event_times_s", supplied_events)):
            if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
                raise ValueError(f"{label} must be a sequence of explicit times")
            previous = None
            for raw_time in events:
                if isinstance(raw_time, bool):
                    raise ValueError(f"{label} must contain numeric times")
                try:
                    event_time = float(raw_time)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{label} must contain numeric times") from exc
                if not math.isfinite(event_time) or event_time < 0.0:
                    raise ValueError(f"{label} must be finite and non-negative")
                if previous is not None and event_time <= previous:
                    raise ValueError(f"{label} must be strictly increasing")
                validated_events.append(event_time)
                previous = event_time
        self.command_event_times_s = tuple(sorted(set(validated_events)))
        if (
            not isinstance(pressure_solver_max_nfev, int)
            or isinstance(pressure_solver_max_nfev, bool)
            or pressure_solver_max_nfev < 1
        ):
            raise ValueError("pressure_solver_max_nfev must be a positive integer")
        self.pressure_solver_max_nfev = int(pressure_solver_max_nfev)
        self._boundary_nodes_by_owner = {}
        declared_owners = {}
        for name, model in self.dynamic_nodes.items():
            if model.boundary_thermo is None:
                if model.boundary_derivative is not None or model.representative_boundary is not None:
                    raise ValueError("multi-boundary callbacks must be supplied together")
                aliases = (name,)
            else:
                aliases = tuple(model.boundary_states_for(model.initial_state, owner_name=name))
            for alias in aliases:
                if alias in declared_owners:
                    raise ValueError(f"dynamic boundary {alias} is owned by more than one stored state")
                declared_owners[alias] = name
            self._boundary_nodes_by_owner[name] = aliases
        missing = [alias for aliases in self._boundary_nodes_by_owner.values()
                   for alias in aliases if alias not in network.boundaries]
        if missing:
            raise KeyError("Dynamic nodes must be boundary nodes: " + ", ".join(missing))
        if len(self.dynamic_nodes) != len(set(self.dynamic_nodes)):
            raise ValueError("Duplicate dynamic node name")

    @staticmethod
    def _node_flow(node: str, flow: LinkSolution, states: Mapping[str, ThermoState]) -> MassEnergyFlow | None:
        if node not in (flow.left, flow.right):
            return None
        mass = flow.mass_flow_kg_s
        if node == flow.right:
            if mass >= 0.0:
                return MassEnergyFlow(mass, flow.transmitted_specific_enthalpy_J_kg, source=flow.upstream_port_id or flow.name)
            return MassEnergyFlow(mass, states[node].specific_enthalpy_J_kg, source=flow.upstream_port_id or flow.name)
        if mass >= 0.0:
            return MassEnergyFlow(-mass, states[node].specific_enthalpy_J_kg, source=flow.upstream_port_id or flow.name)
        return MassEnergyFlow(-mass, flow.transmitted_specific_enthalpy_J_kg, source=flow.upstream_port_id or flow.name)

    def _evaluate_stage(
        self,
        states: Mapping[str, object],
        time_s: float,
        *,
        command_time_s: float | None = None,
    ) -> tuple[
        NetworkSolution,
        dict[str, ThermoState],
        dict[str, tuple[MassEnergyFlow, ...]],
        dict[str, object],
        dict[str, float],
    ]:
        thermo_states = {}
        port_states = {}
        for name, state in states.items():
            model = self.dynamic_nodes[name]
            declared = model.boundary_states_for(state, owner_name=name)
            if set(declared) != set(self._boundary_nodes_by_owner[name]):
                raise ValueError(f"dynamic node {name} boundary aliases changed during simulation")
            port_states.update(declared)
            if model.boundary_thermo is None:
                thermo_states[name] = declared[name]
            elif model.representative_boundary is not None:
                thermo_states[name] = declared[model.representative_boundary]
            else:
                thermo_states[name] = model.thermo(state)
        # Publish the current RK4 stage before evaluating commands.  Pressure-
        # triggered controls must read the stage state, not the previous
        # network boundary state; scheduled commands remain unaffected.
        self.network.set_boundary_states(port_states)
        if self.command_callback is not None:
            self.command_callback(time_s if command_time_s is None else command_time_s,
                                  self.network)
        solution = self.network.solve(max_pressure_nfev=self.pressure_solver_max_nfev)
        if not solution.pressure_solver_success:
            raise NetworkConvergenceError(time_s, solution)
        node_flows: dict[str, tuple[MassEnergyFlow, ...]] = {}
        derivatives: dict[str, object] = {}
        ambient_heat: dict[str, float] = {}
        for name, model in self.dynamic_nodes.items():
            flows_by_boundary = {
                alias: tuple(stream for link in solution.link_flows.values()
                             for stream in (self._node_flow(alias, link, solution.node_states),)
                             if stream is not None)
                for alias in self._boundary_nodes_by_owner[name]}
            flows = tuple(stream for alias_flows in flows_by_boundary.values() for stream in alias_flows)
            node_flows[name] = flows
            derivative = (model.derivative(states[name], flows, time_s) if model.boundary_derivative is None
                          else model.boundary_derivative(states[name], flows_by_boundary, time_s))
            derivatives[name] = derivative
            raw_heat = (getattr(derivative, "ambient_heat_W", 0.0) if model.boundary_heat is None
                        else model.boundary_heat(states[name], flows, time_s, derivative))
            if isinstance(raw_heat, bool) or not math.isfinite(float(raw_heat)):
                raise ValueError(f"dynamic node {name} boundary heat must be finite and numeric")
            ambient_heat[name] = float(raw_heat)
        return solution, thermo_states, node_flows, derivatives, ambient_heat

    @staticmethod
    def _stage_boundary_ledger(
        thermo_states: Mapping[str, ThermoState],
        node_flows: Mapping[str, tuple[MassEnergyFlow, ...]],
        derivatives: Mapping[str, object],
        external_heat_W: Mapping[str, float] | None = None,
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Return signed mass and total energy rates for one RK4 stage.

        ``MassEnergyFlow`` already carries the receiving-node sign and the
        correct upstream enthalpy.  Dynamic models may additionally expose an
        ``ambient_heat_W`` term, which belongs in the control-volume energy
        ledger but not in the transported enthalpy output.
        """

        mass_rates = {
            name: sum(flow.mass_flow_kg_s for flow in node_flows[name])
            for name in node_flows
        }
        energy_rates = {
            name: sum(flow.enthalpy_flow_W for flow in node_flows[name])
            + (float(getattr(derivatives[name], "ambient_heat_W", 0.0)) if external_heat_W is None
               else external_heat_W[name])
            for name in node_flows
        }
        return mass_rates, energy_rates

    def simulate(
        self,
        duration_s: float,
        time_step_s: float,
    ) -> list[DynamicNetworkStepResult[object]]:
        """Integrate all nodes with RK4, stopping at explicit command events.

        ``time_step_s`` is the maximum step; returned times include event
        endpoints. The terminal RK4 stage uses the command's left limit at a
        switch. Output flow uses the command at the exact event time, while
        stored mass/energy remain continuous. Bound command schedules supply
        event times automatically; custom callbacks must declare them.

        Every RK4 and output junction solve must converge before its streams
        are consumed. A ``NetworkConvergenceError`` reports the failed stage
        and solution; no failed step is returned as a completed prediction.
        """
        for label, raw in (("duration_s", duration_s), ("time_step_s", time_step_s)):
            if isinstance(raw, bool):
                raise ValueError(f"{label} must be finite and positive")
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label} must be finite and positive") from exc
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{label} must be finite and positive")
        duration_s, time_step_s = float(duration_s), float(time_step_s)
        states: dict[str, object] = {
            name: model.initial_state for name, model in self.dynamic_nodes.items()
        }
        cumulative_mass = {name: 0.0 for name in states}
        cumulative_energy = {name: 0.0 for name in states}
        cumulative_transport_energy = {name: 0.0 for name in states}
        cumulative_ambient = {name: 0.0 for name in states}
        cumulative_potential = 0.0
        results: list[DynamicNetworkStepResult[object]] = []
        time_s = 0.0
        while time_s < duration_s:
            emit_step()
            event_index = bisect_right(self.command_event_times_s, time_s)
            next_event = (self.command_event_times_s[event_index]
                          if event_index < len(self.command_event_times_s) else math.inf)
            nominal_end_s = time_s + time_step_s
            # Avoid a spurious terminal step from repeated floating-point
            # addition (e.g. ten 0.1-s steps). Event times still take priority,
            # including an event within one representable float of the end.
            if math.isfinite(nominal_end_s) and abs(nominal_end_s-duration_s) <= (
                4.0 * max(math.ulp(nominal_end_s), math.ulp(duration_s))
            ):
                nominal_end_s = duration_s
            step_end_s = min(nominal_end_s, duration_s, next_event)
            dt = step_end_s - time_s
            if dt <= 0.0:
                raise ValueError("time step cannot advance the floating-point time coordinate")
            command_end_s = (math.nextafter(step_end_s, -math.inf)
                             if step_end_s == next_event else step_end_s)
            midpoint_s = time_s + dt / 2.0
            # At adjacent floating-point times the midpoint can round to the
            # event itself. Keep every integration stage on the left command
            # branch even when no interior float can represent the midpoint.
            midpoint_command_s = min(midpoint_s, command_end_s)
            s1, t1, f1, k1, a1 = self._evaluate_stage(states, time_s)
            m1, e1 = self._stage_boundary_ledger(t1, f1, k1, a1)
            half_1 = {
                name: self.dynamic_nodes[name].advance(states[name], k1[name], dt / 2.0)
                for name in states
            }
            s2, t2, f2, k2, a2 = self._evaluate_stage(
                half_1, midpoint_s, command_time_s=midpoint_command_s)
            m2, e2 = self._stage_boundary_ledger(t2, f2, k2, a2)
            half_2 = {
                name: self.dynamic_nodes[name].advance(states[name], k2[name], dt / 2.0)
                for name in states
            }
            s3, t3, f3, k3, a3 = self._evaluate_stage(
                half_2, midpoint_s, command_time_s=midpoint_command_s)
            m3, e3 = self._stage_boundary_ledger(t3, f3, k3, a3)
            full_3 = {
                name: self.dynamic_nodes[name].advance(states[name], k3[name], dt)
                for name in states
            }
            s4, t4, f4, k4, a4 = self._evaluate_stage(
                full_3, step_end_s, command_time_s=command_end_s)
            m4, e4 = self._stage_boundary_ledger(t4, f4, k4, a4)
            states = {
                name: self.dynamic_nodes[name].advance(
                    states[name],
                    self.dynamic_nodes[name].combine_derivatives(k1[name], k2[name], k3[name], k4[name]),
                    dt,
                )
                for name in states
            }
            time_s = step_end_s
            cumulative_potential += dt*(
                sum(flow.potential_energy_change_W for flow in s1.link_flows.values())
                +2*sum(flow.potential_energy_change_W for flow in s2.link_flows.values())
                +2*sum(flow.potential_energy_change_W for flow in s3.link_flows.values())
                +sum(flow.potential_energy_change_W for flow in s4.link_flows.values()))/6
            for name in states:
                cumulative_mass[name] += dt * (
                    m1[name] + 2.0 * m2[name] + 2.0 * m3[name] + m4[name]
                ) / 6.0
                cumulative_energy[name] += dt * (
                    e1[name] + 2.0 * e2[name] + 2.0 * e3[name] + e4[name]
                ) / 6.0
                cumulative_transport_energy[name] += dt * (
                    (e1[name] - a1[name])
                    + 2.0 * (e2[name] - a2[name])
                    + 2.0 * (e3[name] - a3[name])
                    + (e4[name] - a4[name])
                ) / 6.0
                cumulative_ambient[name] += dt * (
                    a1[name] + 2.0 * a2[name] + 2.0 * a3[name] + a4[name]
                ) / 6.0
            solution, thermo_states, node_flows, _unused, ambient_heat = self._evaluate_stage(states, time_s)
            node_mass = {
                name: sum(flow.mass_flow_kg_s for flow in node_flows[name])
                for name in states
            }
            node_energy = {
                name: sum(flow.enthalpy_flow_W for flow in node_flows[name])
                for name in states
            }
            network_mass_flow = sum(node_mass.values())
            network_transport_energy = sum(node_energy.values())
            network_ambient_heat = sum(ambient_heat.values())
            results.append(DynamicNetworkStepResult(
                time_s=time_s,
                states=dict(states),
                thermo_states=dict(thermo_states),
                port_thermo_states={alias: solution.node_states[alias]
                                   for aliases in self._boundary_nodes_by_owner.values() for alias in aliases},
                network_solution=solution,
                node_flows=node_flows,
                node_mass_flow_kg_s=node_mass,
                node_transport_energy_W=node_energy,
                node_ambient_heat_W=ambient_heat,
                cumulative_boundary_mass_kg=dict(cumulative_mass),
                cumulative_boundary_energy_J=dict(cumulative_energy),
                cumulative_boundary_transport_energy_J=dict(cumulative_transport_energy),
                cumulative_boundary_ambient_heat_J=dict(cumulative_ambient),
                network_boundary_mass_flow_kg_s=network_mass_flow,
                network_boundary_transport_energy_W=network_transport_energy,
                network_boundary_ambient_heat_W=network_ambient_heat,
                cumulative_network_boundary_mass_kg=sum(cumulative_mass.values()),
                cumulative_network_boundary_energy_J=sum(cumulative_energy.values()),
                cumulative_network_boundary_transport_energy_J=sum(cumulative_transport_energy.values()),
                cumulative_network_boundary_ambient_heat_J=sum(cumulative_ambient.values()),
                link_potential_energy_change_W=sum(flow.potential_energy_change_W for flow in solution.link_flows.values()),
                cumulative_link_potential_energy_change_J=cumulative_potential,
            ))
            emit_step(results[-1])
        return results
