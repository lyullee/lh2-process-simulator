"""Hydrostatic DPT observations with explicit high/low impulse-line heads.

All heights share one datum, pressures are Pa and densities kg/m3. This is
an observation transform, not a fluid state or a coefficient fitting model.
Leg densities are declared mean column densities; phase/thermal profiles,
trim, mounting height and HART assignments are never inferred from tags.
"""

from dataclasses import dataclass
import math

from .geometry import HydrostaticDifferentialPressure
from .hart import HartDynamicVariables, HartObservationState, PMD75BHartObservation


def _finite(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, not bool")
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class ImpulseLineHydrostaticDP:
    taps: HydrostaticDifferentialPressure
    sensor_height_m: float
    high_leg_density_kg_m3: float
    low_leg_density_kg_m3: float
    position_adjustment_Pa: float = 0.0

    def __post_init__(self):
        if not isinstance(self.taps, HydrostaticDifferentialPressure):
            raise TypeError("taps must be HydrostaticDifferentialPressure")
        for name in ("sensor_height_m", "high_leg_density_kg_m3", "low_leg_density_kg_m3", "position_adjustment_Pa"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        for name in ("lower_tap_height_m", "upper_tap_height_m", "gravity_m_s2"):
            _finite(getattr(self.taps, name), name)
        if self.high_leg_density_kg_m3 <= 0 or self.low_leg_density_kg_m3 <= 0:
            raise ValueError("impulse-line densities must be positive")

    @property
    def impulse_head_difference_Pa(self):
        return self.taps.gravity_m_s2*(
            self.high_leg_density_kg_m3*(self.taps.lower_tap_height_m-self.sensor_height_m)
            -self.low_leg_density_kg_m3*(self.taps.upper_tap_height_m-self.sensor_height_m))

    def evaluate(self, liquid_height_m, liquid_density_kg_m3, vapor_density_kg_m3):
        height = _finite(liquid_height_m, "liquid_height_m")
        liquid = _finite(liquid_density_kg_m3, "liquid_density_kg_m3")
        vapor = _finite(vapor_density_kg_m3, "vapor_density_kg_m3")
        if not liquid > vapor > 0:
            raise ValueError("liquid density must exceed positive vapor density")
        raw = self.taps.evaluate(height, liquid, vapor)+self.impulse_head_difference_Pa
        return {"raw_sensor_differential_pressure_Pa": raw,
                "position_adjusted_differential_pressure_Pa": raw+self.position_adjustment_Pa,
                "impulse_head_difference_Pa": self.impulse_head_difference_Pa}

    def liquid_height_from_adjusted_pressure(self, pressure_Pa, liquid_density_kg_m3, vapor_density_kg_m3):
        pressure = _finite(pressure_Pa, "pressure_Pa")
        liquid = _finite(liquid_density_kg_m3, "liquid_density_kg_m3")
        vapor = _finite(vapor_density_kg_m3, "vapor_density_kg_m3")
        if not liquid > vapor > 0:
            raise ValueError("liquid density must exceed positive vapor density")
        return self.taps.height_from_differential_pressure(
            pressure-self.position_adjustment_Pa-self.impulse_head_difference_Pa, liquid, vapor)


@dataclass(frozen=True)
class TankDptObservationState:
    """State carried by the tank-to-DPT observation adapter."""

    hart_state: HartObservationState

    def __post_init__(self):
        if not isinstance(self.hart_state, HartObservationState):
            raise TypeError("hart_state must be HartObservationState")


@dataclass(frozen=True)
class TankDptObservationResult:
    """One physical tank observation before any historian unit conversion.

    ``static_pressure_Pa`` is the explicit tank pressure observable.  The
    DPT values are the same differential-pressure cell viewed at two points in
    the measurement path: ``raw_dpt_Pa`` is before impulse-line and zero
    adjustment, while ``position_adjusted_dpt_Pa`` is the value sent to the
    HART PV path.  SV and TV are temperature observables supplied by the
    instrument model; they are not inferred from the tank pressure.
    """

    static_pressure_Pa: float
    raw_dpt_Pa: float
    position_adjusted_dpt_Pa: float
    hart: HartDynamicVariables

    def __post_init__(self):
        for name in ("static_pressure_Pa", "raw_dpt_Pa", "position_adjusted_dpt_Pa"):
            _finite(getattr(self, name), name)
        if not isinstance(self.hart, HartDynamicVariables):
            raise TypeError("hart must be HartDynamicVariables")

    def as_dict(self) -> dict[str, float]:
        values = {
            "static_pressure_Pa": self.static_pressure_Pa,
            "raw_dpt_Pa": self.raw_dpt_Pa,
            "position_adjusted_dpt_Pa": self.position_adjusted_dpt_Pa,
        }
        values.update({f"HART_{key}": value for key, value in self.hart.as_dict().items()})
        return values


class TankDptObservation:
    """Compose tank hydrostatics with an explicit PMD75B HART observation.

    The adapter is intentionally a measurement-layer composition.  It does
    not infer a DPT range, pressure basis, tap location, HART address, or
    level scaling.  The caller supplies the tank static pressure separately,
    because the DPT PV/QV pair is not an independent PT measurement.
    """

    def __init__(
        self,
        dpt: ImpulseLineHydrostaticDP,
        hart: PMD75BHartObservation | None = None,
    ):
        if not isinstance(dpt, ImpulseLineHydrostaticDP):
            raise TypeError("dpt must be ImpulseLineHydrostaticDP")
        if hart is not None and not isinstance(hart, PMD75BHartObservation):
            raise TypeError("hart must be PMD75BHartObservation or None")
        self.dpt = dpt
        self.hart = hart if hart is not None else PMD75BHartObservation()

    def initialize(self, position_adjusted_pressure_Pa: float) -> TankDptObservationState:
        adjusted = _finite(position_adjusted_pressure_Pa, "position_adjusted_pressure_Pa")
        return TankDptObservationState(self.hart.initialize(adjusted))

    def advance(
        self,
        state: TankDptObservationState,
        *,
        liquid_height_m: float,
        static_pressure_Pa: float,
        liquid_density_kg_m3: float,
        vapor_density_kg_m3: float,
        sensor_temperature_K: float,
        electronics_temperature_K: float,
        time_step_s: float,
    ) -> tuple[TankDptObservationState, TankDptObservationResult]:
        if not isinstance(state, TankDptObservationState):
            raise TypeError("state must be TankDptObservationState")
        static_pressure = _finite(static_pressure_Pa, "static_pressure_Pa")
        dpt = self.dpt.evaluate(
            liquid_height_m,
            liquid_density_kg_m3,
            vapor_density_kg_m3,
        )
        next_hart_state, hart_variables = self.hart.advance(
            state.hart_state,
            raw_sensor_pressure=dpt["raw_sensor_differential_pressure_Pa"],
            position_adjusted_pressure=dpt["position_adjusted_differential_pressure_Pa"],
            sensor_temperature=sensor_temperature_K,
            electronics_temperature=electronics_temperature_K,
            time_step_s=time_step_s,
        )
        return (
            TankDptObservationState(next_hart_state),
            TankDptObservationResult(
                static_pressure_Pa=static_pressure,
                raw_dpt_Pa=dpt["raw_sensor_differential_pressure_Pa"],
                position_adjusted_dpt_Pa=dpt["position_adjusted_differential_pressure_Pa"],
                hart=hart_variables,
            ),
        )
