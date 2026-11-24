"""Explicit connected-line geometry for tank pressure boundaries.

The helper turns a reviewed line size, route length, and material closure into
the two values consumed by :class:`LayeredTankParameters`.  Route length is
required from the caller; no installed length or boundary is inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping


def _positive(value: float, name: str, *, allow_zero: bool = False) -> float:
    result = float(value)
    if not math.isfinite(result) or (result < 0.0 if allow_zero else result <= 0.0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be finite and {qualifier}")
    return result


@dataclass(frozen=True)
class ImpulseLineConnectionGeometry:
    """Geometry and material closure for pressure-equal connected lines.

    ``line_count`` represents parallel impulse legs.  The resulting volume and
    wall heat capacity are explicit authoring inputs; they are not inferred
    from a DPT reading or selected against a pressure trace.
    """

    inner_diameter_m: float
    outer_diameter_m: float
    route_length_m: float
    line_count: int = 1
    wall_density_kg_m3: float = 7_950.0
    wall_specific_heat_J_kgK: float = 500.0

    def __post_init__(self) -> None:
        inner = _positive(self.inner_diameter_m, "inner_diameter_m")
        outer = _positive(self.outer_diameter_m, "outer_diameter_m")
        length = _positive(self.route_length_m, "route_length_m", allow_zero=True)
        density = _positive(self.wall_density_kg_m3, "wall_density_kg_m3")
        heat = _positive(self.wall_specific_heat_J_kgK, "wall_specific_heat_J_kgK")
        if outer <= inner:
            raise ValueError("outer_diameter_m must exceed inner_diameter_m")
        if not isinstance(self.line_count, int) or isinstance(self.line_count, bool):
            raise ValueError("line_count must be an integer")
        if self.line_count < 1:
            raise ValueError("line_count must be at least one")
        object.__setattr__(self, "inner_diameter_m", inner)
        object.__setattr__(self, "outer_diameter_m", outer)
        object.__setattr__(self, "route_length_m", length)
        object.__setattr__(self, "wall_density_kg_m3", density)
        object.__setattr__(self, "wall_specific_heat_J_kgK", heat)

    @classmethod
    def from_center_connection_register(
        cls,
        register: Mapping[str, Any],
        *,
        route_length_m: float | None = None,
    ) -> "ImpulseLineConnectionGeometry":
        """Build geometry from an explicit center connection register.

        The register may provide a confirmed ``actual_installed_route_length_m``
        or the caller may supply one explicitly.  If neither is present, the
        method fails closed; an envelope entry is never selected from an
        observation trace.  Envelope values are used only as dimensional
        checks when the register contains them.
        """

        if not isinstance(register, Mapping):
            raise TypeError("connection register must be a mapping")
        dead_volume = register.get("dead_volume")
        if not isinstance(dead_volume, Mapping):
            raise ValueError("connection register.dead_volume must be an object")
        line_basis = dead_volume.get("line_basis")
        if not isinstance(line_basis, Mapping):
            raise ValueError("connection register.dead_volume.line_basis must be an object")

        resolved_length = route_length_m
        if resolved_length is None:
            resolved_length = dead_volume.get("actual_installed_route_length_m")
        if resolved_length is None:
            raise ValueError(
                "installed route length is unresolved; provide an explicit route_length_m "
                "or a confirmed actual_installed_route_length_m"
            )

        envelope = dead_volume.get("route_length_envelope", ())
        if envelope is not None and not isinstance(envelope, (list, tuple)):
            raise ValueError("connection register.dead_volume.route_length_envelope must be a list")
        # Validate any supplied envelope rows without using them to choose a
        # route.  This catches stale or malformed evidence before construction.
        for index, row in enumerate(envelope or ()):
            if not isinstance(row, Mapping) or row.get("route_length_m") is None:
                raise ValueError(f"route_length_envelope[{index}] requires route_length_m")
            _positive(row["route_length_m"], f"route_length_envelope[{index}].route_length_m", allow_zero=True)

        return cls(
            inner_diameter_m=float(line_basis["inner_diameter_m"]),
            outer_diameter_m=float(line_basis["outer_diameter_m"]),
            route_length_m=float(resolved_length),
            line_count=int(line_basis.get("line_count", 1)),
        )

    @property
    def vapor_volume_m3(self) -> float:
        """Pressure-equal connected vapor volume."""

        return (
            math.pi * self.inner_diameter_m**2 / 4.0
            * self.route_length_m
            * self.line_count
        )

    @property
    def wall_mass_kg(self) -> float:
        """Metal mass for the annular line wall."""

        return (
            math.pi * (self.outer_diameter_m**2 - self.inner_diameter_m**2) / 4.0
            * self.route_length_m
            * self.line_count
            * self.wall_density_kg_m3
        )

    @property
    def wall_heat_capacity_J_K(self) -> float:
        """Connected metal heat capacity supplied to the tank model."""

        return self.wall_mass_kg * self.wall_specific_heat_J_kgK

    def as_layered_tank_connection(self) -> dict[str, float]:
        """Return explicit ``LayeredTankParameters`` connection values."""

        return {
            "connected_vapor_volume_m3": self.vapor_volume_m3,
            "connected_wall_heat_capacity_J_K": self.wall_heat_capacity_J_K,
        }


__all__ = ["ImpulseLineConnectionGeometry"]
