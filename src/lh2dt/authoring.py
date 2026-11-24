"""Backend contract for a modular LH2 receiving-terminal authoring tool.

The numerical component classes do not know about center equipment tags.  This
module places a small design layer above them: a reusable template can be
instantiated many times, resized with an explicit physical scaling law, and
connected or detached through typed ports.  Every placed instance is still
built by :func:`lh2dt.component_factory.build_asset_model`, so authoring never
bypasses the first-principles parameter checks.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .catalog import REQUIRED_PARAMETERS
from .component_factory import AssetModelBuild, build_asset_model
from .geometry import HorizontalVesselGeometry
from .topology import FacilityTopology, TopologyConnection


_DIRECTIONS = {"inlet", "outlet", "bidirectional"}
_MEDIA = {"liquid_hydrogen", "hydrogen_vapor", "hydrogen"}
_STORAGE_MODELS = {
    "homogeneous_tank", "stratified_tank", "radial_axial_tank", "layered_tank"
}
_DIRECT_NETWORK_LINK_MODELS = {
    "pipe", "hem_pipe", "virtual_pump", "mapped_pump", "valve",
    "check_valve", "pressure_relief_valve", "temperature_protection_valve",
    "vent_stack",
}
_ADAPTER_REQUIRED_MODELS = {
    "vaporizer", "vaporizer_resistance_network", "ambient_air_vaporizer", "reliquefier",
    "dynamic_vaporizer", "dynamic_reliquefier", "dynamic_hem_pipe",
    "pump", "compressor",
}


def _positive_finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite and positive")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite and positive") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be finite and positive")
    return result


@dataclass(frozen=True)
class AuthoringPort:
    """One connection point exposed by an authoring template."""

    port_id: str
    medium: str
    direction: str
    role: str = ""

    def __post_init__(self) -> None:
        if not self.port_id.strip():
            raise ValueError("port_id must be non-empty")
        if self.medium not in _MEDIA:
            raise ValueError(f"unsupported port medium: {self.medium}")
        if self.direction not in _DIRECTIONS:
            raise ValueError(f"unsupported port direction: {self.direction}")


@dataclass(frozen=True)
class ComponentTemplate:
    """Reusable component definition with an explicit capacity scaling law.

    ``scale_exponents`` maps a top-level model parameter to an exponent of the
    requested capacity ratio.  For example, a geometrically similar tank uses
    exponent 1 for volume, 1/3 for lengths, and 2/3 for area-controlled heat
    transfer and wall heat capacity.  Parameters absent from the map remain
    unchanged, including pressure and temperature limits.
    """

    template_id: str
    component_model: str
    base_parameters: Mapping[str, Any]
    parameter_source: str
    ports: tuple[AuthoringPort, ...]
    scale_exponents: Mapping[str, float]
    scale_basis: str
    nominal_capacity: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.template_id.strip():
            raise ValueError("template_id must be non-empty")
        if self.component_model not in REQUIRED_PARAMETERS:
            raise ValueError(f"unknown component model: {self.component_model}")
        if not self.parameter_source.strip():
            raise ValueError("parameter_source must be non-empty")
        if not self.scale_basis.strip():
            raise ValueError("scale_basis must be non-empty")
        port_ids = [port.port_id for port in self.ports]
        if not port_ids or len(set(port_ids)) != len(port_ids):
            raise ValueError("template ports must be non-empty and unique")
        missing = [
            name for name in REQUIRED_PARAMETERS[self.component_model]
            if self.base_parameters.get(name) is None
        ]
        if missing:
            raise ValueError(
                f"template {self.template_id} is missing parameters: {', '.join(missing)}"
            )
        for name, exponent in self.scale_exponents.items():
            if name not in self.base_parameters:
                raise ValueError(f"scale parameter {name} is absent from base_parameters")
            value = self.base_parameters[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"scale parameter {name} must be numeric")
            if isinstance(exponent, bool) or not math.isfinite(float(exponent)):
                raise ValueError(f"scale exponent for {name} must be finite")

        build = build_asset_model(
            {
                "asset_id": self.template_id,
                "component_model": self.component_model,
                "model_parameters": deepcopy(dict(self.base_parameters)),
                "source": self.parameter_source,
            }
        )
        if not build.ready:
            detail = "; ".join((*build.missing_parameters, *build.issues))
            raise ValueError(f"template {self.template_id} cannot be built: {detail}")

    def scaled_parameters(self, capacity_scale: float) -> dict[str, Any]:
        scale = _positive_finite(capacity_scale, "capacity_scale")
        parameters = deepcopy(dict(self.base_parameters))
        for name, exponent in self.scale_exponents.items():
            parameters[name] = float(parameters[name]) * scale ** float(exponent)
        return parameters


@dataclass(frozen=True)
class AuthoredComponent:
    """One independent instance placed on an editable facility canvas."""

    instance_id: str
    template_id: str
    component_model: str
    model_parameters: Mapping[str, Any]
    parameter_source: str
    ports: tuple[AuthoringPort, ...]
    capacity_scale: float
    parameter_overrides: Mapping[str, Any] = field(default_factory=dict)

    def port(self, port_id: str) -> AuthoringPort:
        for port in self.ports:
            if port.port_id == port_id:
                return port
        raise KeyError(f"component {self.instance_id} has no port {port_id}")

    def asset_record(self) -> dict[str, Any]:
        return {
            "asset_id": self.instance_id,
            "component_model": self.component_model,
            "model_parameters": deepcopy(dict(self.model_parameters)),
            "source": self.parameter_source,
            "validation_status": "authoring_template_instance",
        }

    def build(self) -> AssetModelBuild:
        return build_asset_model(self.asset_record())


@dataclass(frozen=True)
class AuthoredConnection:
    connection_id: str
    source_component_id: str
    source_port_id: str
    target_component_id: str
    target_port_id: str
    service: str


@dataclass(frozen=True)
class FacilityDraftAudit:
    component_count: int
    connection_count: int
    ready_component_count: int
    invalid_component_ids: tuple[str, ...]
    isolated_component_ids: tuple[str, ...]

    @property
    def numerically_buildable(self) -> bool:
        return self.component_count > 0 and not self.invalid_component_ids


@dataclass(frozen=True)
class AuthoringExecutionNode:
    """One hydraulic node produced from connected authoring ports."""

    node_id: str
    node_kind: str
    port_refs: tuple[str, ...]


@dataclass(frozen=True)
class AuthoringExecutionComponent:
    """A numerical component and the hydraulic nodes attached to its ports."""

    instance_id: str
    component_model: str
    execution_role: str
    port_nodes: Mapping[str, str]


@dataclass(frozen=True)
class AuthoringExecutionPlan:
    """Deterministic bridge between the editor graph and numerical assembly.

    Direct network links can be inserted into ``SteadyNetwork`` after boundary
    states and junction initial guesses are supplied. Thermal process units
    need an explicit hydraulic element and operating condition, so they are
    marked ``adapter_required`` instead of receiving invented inputs.
    """

    design_id: str
    nodes: tuple[AuthoringExecutionNode, ...]
    components: tuple[AuthoringExecutionComponent, ...]
    exposed_port_refs: tuple[str, ...]
    issues: tuple[str, ...]

    @property
    def structurally_ready(self) -> bool:
        return not self.issues

    @property
    def direct_network_link_count(self) -> int:
        return sum(item.execution_role == "direct_network_link" for item in self.components)

    @property
    def adapter_required_count(self) -> int:
        return sum(item.execution_role == "adapter_required" for item in self.components)

    def as_dict(self) -> dict[str, Any]:
        return {
            "design_id": self.design_id,
            "nodes": [
                {
                    "node_id": item.node_id,
                    "node_kind": item.node_kind,
                    "port_refs": list(item.port_refs),
                }
                for item in self.nodes
            ],
            "components": [
                {
                    "instance_id": item.instance_id,
                    "component_model": item.component_model,
                    "execution_role": item.execution_role,
                    "port_nodes": dict(item.port_nodes),
                }
                for item in self.components
            ],
            "exposed_port_refs": list(self.exposed_port_refs),
            "issues": list(self.issues),
            "structurally_ready": self.structurally_ready,
            "direct_network_link_count": self.direct_network_link_count,
            "adapter_required_count": self.adapter_required_count,
        }


@dataclass(frozen=True)
class ComponentLibrary:
    templates: Mapping[str, ComponentTemplate]

    def template(self, template_id: str) -> ComponentTemplate:
        try:
            return self.templates[template_id]
        except KeyError as exc:
            raise KeyError(f"unknown component template: {template_id}") from exc

    def instantiate(
        self,
        template_id: str,
        instance_id: str,
        *,
        capacity_scale: float = 1.0,
        parameter_overrides: Mapping[str, Any] | None = None,
    ) -> AuthoredComponent:
        if not str(instance_id).strip():
            raise ValueError("instance_id must be non-empty")
        template = self.template(template_id)
        scale = _positive_finite(capacity_scale, "capacity_scale")
        overrides = deepcopy(dict(parameter_overrides or {}))
        parameters = template.scaled_parameters(scale)
        parameters.update(overrides)
        instance = AuthoredComponent(
            instance_id=str(instance_id),
            template_id=template.template_id,
            component_model=template.component_model,
            model_parameters=parameters,
            parameter_source=(
                f"{template.parameter_source}; authoring capacity scale={scale:g}; "
                f"scaling basis={template.scale_basis}"
            ),
            ports=template.ports,
            capacity_scale=scale,
            parameter_overrides=overrides,
        )
        build = instance.build()
        if not build.ready:
            detail = "; ".join((*build.missing_parameters, *build.issues))
            raise ValueError(f"component {instance_id} cannot be built: {detail}")
        return instance


def load_component_library(path: str | Path) -> ComponentLibrary:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != "0.1":
        raise ValueError("unsupported authoring component-library schema")
    raw_templates = data.get("templates")
    if not isinstance(raw_templates, list) or not raw_templates:
        raise ValueError("component library must contain templates")
    templates: dict[str, ComponentTemplate] = {}
    for raw in raw_templates:
        ports = tuple(
            AuthoringPort(
                port_id=str(item.get("port_id", "")),
                medium=str(item.get("medium", "")),
                direction=str(item.get("direction", "")),
                role=str(item.get("role", "")),
            )
            for item in raw.get("ports", [])
        )
        template = ComponentTemplate(
            template_id=str(raw.get("template_id", "")),
            component_model=str(raw.get("component_model", "")),
            base_parameters=raw.get("base_parameters", {}),
            parameter_source=str(raw.get("parameter_source", "")),
            ports=ports,
            scale_exponents=raw.get("scale_exponents", {}),
            scale_basis=str(raw.get("scale_basis", "")),
            nominal_capacity=raw.get("nominal_capacity", {}),
        )
        if template.template_id in templates:
            raise ValueError(f"duplicate component template: {template.template_id}")
        templates[template.template_id] = template
    return ComponentLibrary(templates=templates)


def _media_compatible(source: str, target: str) -> bool:
    return source == target or source == "hydrogen" or target == "hydrogen"


class FacilityDraft:
    """Mutable facility graph intended to sit behind a graphical editor."""

    def __init__(self, design_id: str):
        if not str(design_id).strip():
            raise ValueError("design_id must be non-empty")
        self.design_id = str(design_id)
        self.components: dict[str, AuthoredComponent] = {}
        self.connections: dict[str, AuthoredConnection] = {}

    def place_component(
        self,
        library: ComponentLibrary,
        template_id: str,
        instance_id: str,
        *,
        capacity_scale: float = 1.0,
        parameter_overrides: Mapping[str, Any] | None = None,
    ) -> AuthoredComponent:
        if instance_id in self.components:
            raise ValueError(f"component instance already exists: {instance_id}")
        component = library.instantiate(
            template_id,
            instance_id,
            capacity_scale=capacity_scale,
            parameter_overrides=parameter_overrides,
        )
        self.components[instance_id] = component
        return component

    def duplicate_component(
        self,
        library: ComponentLibrary,
        source_instance_id: str,
        new_instance_id: str,
    ) -> AuthoredComponent:
        source = self.components[source_instance_id]
        return self.place_component(
            library,
            source.template_id,
            new_instance_id,
            capacity_scale=source.capacity_scale,
            parameter_overrides=source.parameter_overrides,
        )

    def resize_component(
        self,
        library: ComponentLibrary,
        instance_id: str,
        capacity_scale: float,
    ) -> AuthoredComponent:
        source = self.components[instance_id]
        template = library.template(source.template_id)
        # Re-apply the declared capacity law. Previous overrides of scaled
        # fields are cleared so they cannot silently defeat a resize request.
        retained_overrides = {
            key: value for key, value in source.parameter_overrides.items()
            if key not in template.scale_exponents
        }
        replacement = library.instantiate(
            source.template_id,
            instance_id,
            capacity_scale=capacity_scale,
            parameter_overrides=retained_overrides,
        )
        self.components[instance_id] = replacement
        return replacement

    def reshape_horizontal_tank(
        self,
        library: ComponentLibrary,
        instance_id: str,
        *,
        inner_diameter_m: float,
        straight_length_m: float,
        head_depth_m: float,
        preserve_thermal_areal_properties: bool = True,
    ) -> AuthoredComponent:
        """Change vessel proportions using exact horizontal-vessel geometry.

        If areal properties are preserved, wall heat capacity and total
        conductances scale with inner surface area. This keeps the template's
        material and heat-transfer basis while avoiding plant-data fitting.
        Pressure class and unrelated parameters remain unchanged.
        """

        source = self.components[instance_id]
        if source.component_model != "homogeneous_tank":
            raise ValueError("explicit horizontal reshape currently requires homogeneous_tank")
        old_geometry = HorizontalVesselGeometry(
            inner_diameter_m=float(source.model_parameters["inner_diameter_m"]),
            straight_length_m=float(source.model_parameters["straight_length_m"]),
            head_depth_m=float(source.model_parameters["head_depth_m"]),
        )
        new_geometry = HorizontalVesselGeometry(
            inner_diameter_m=inner_diameter_m,
            straight_length_m=straight_length_m,
            head_depth_m=head_depth_m,
        )
        template = library.template(source.template_id)
        base_volume = float(template.base_parameters["volume_m3"])
        capacity_scale = new_geometry.volume_m3 / base_volume
        overrides = deepcopy(dict(source.parameter_overrides))
        overrides.update({
            "inner_diameter_m": float(inner_diameter_m),
            "straight_length_m": float(straight_length_m),
            "head_depth_m": float(head_depth_m),
            "volume_m3": new_geometry.volume_m3,
        })
        if preserve_thermal_areal_properties:
            area_ratio = new_geometry.inner_surface_area_m2 / old_geometry.inner_surface_area_m2
            for name in (
                "wall_heat_capacity_J_K", "ambient_UA_W_K", "fluid_wall_UA_W_K",
            ):
                overrides[name] = float(source.model_parameters[name]) * area_ratio
        replacement = library.instantiate(
            source.template_id,
            instance_id,
            capacity_scale=capacity_scale,
            parameter_overrides=overrides,
        )
        self.components[instance_id] = replacement
        return replacement

    def remove_component(
        self,
        instance_id: str,
        *,
        detach_connections: bool = True,
    ) -> tuple[str, ...]:
        if instance_id not in self.components:
            raise KeyError(f"unknown component instance: {instance_id}")
        attached = tuple(
            connection_id
            for connection_id, connection in self.connections.items()
            if instance_id in {
                connection.source_component_id,
                connection.target_component_id,
            }
        )
        if attached and not detach_connections:
            raise ValueError(
                f"component {instance_id} still has connections: {', '.join(attached)}"
            )
        for connection_id in attached:
            del self.connections[connection_id]
        del self.components[instance_id]
        return attached

    def connect(
        self,
        connection_id: str,
        source_component_id: str,
        source_port_id: str,
        target_component_id: str,
        target_port_id: str,
        *,
        service: str,
    ) -> AuthoredConnection:
        if not connection_id.strip() or connection_id in self.connections:
            raise ValueError("connection_id must be non-empty and unique")
        if source_component_id == target_component_id:
            raise ValueError("a component cannot connect to itself")
        if not service.strip():
            raise ValueError("connection service must be non-empty")
        source_component = self.components[source_component_id]
        target_component = self.components[target_component_id]
        source_port = source_component.port(source_port_id)
        target_port = target_component.port(target_port_id)
        occupied = {
            (item.source_component_id, item.source_port_id)
            for item in self.connections.values()
        } | {
            (item.target_component_id, item.target_port_id)
            for item in self.connections.values()
        }
        if (source_component_id, source_port_id) in occupied:
            raise ValueError(
                f"source port is already connected: {source_component_id}.{source_port_id}"
            )
        if (target_component_id, target_port_id) in occupied:
            raise ValueError(
                f"target port is already connected: {target_component_id}.{target_port_id}"
            )
        if source_port.direction not in {"outlet", "bidirectional"}:
            raise ValueError(f"source port {source_port_id} does not allow outflow")
        if target_port.direction not in {"inlet", "bidirectional"}:
            raise ValueError(f"target port {target_port_id} does not allow inflow")
        if not _media_compatible(source_port.medium, target_port.medium):
            raise ValueError(
                f"incompatible media: {source_port.medium} -> {target_port.medium}"
            )
        connection = AuthoredConnection(
            connection_id=connection_id,
            source_component_id=source_component_id,
            source_port_id=source_port_id,
            target_component_id=target_component_id,
            target_port_id=target_port_id,
            service=service,
        )
        self.connections[connection_id] = connection
        return connection

    def disconnect(self, connection_id: str) -> AuthoredConnection:
        try:
            return self.connections.pop(connection_id)
        except KeyError as exc:
            raise KeyError(f"unknown connection: {connection_id}") from exc

    def audit(self) -> FacilityDraftAudit:
        builds = {key: value.build() for key, value in self.components.items()}
        invalid = tuple(sorted(key for key, build in builds.items() if not build.ready))
        connected: set[str] = set()
        for connection in self.connections.values():
            connected.add(connection.source_component_id)
            connected.add(connection.target_component_id)
        isolated = tuple(sorted(set(self.components) - connected))
        return FacilityDraftAudit(
            component_count=len(self.components),
            connection_count=len(self.connections),
            ready_component_count=sum(build.ready for build in builds.values()),
            invalid_component_ids=invalid,
            isolated_component_ids=isolated,
        )

    def to_topology(self) -> FacilityTopology:
        if not self.components:
            raise ValueError("facility draft must contain at least one component")
        return FacilityTopology(
            schema_version="authoring-0.1",
            resolution="component-instance graph with explicit authoring ports",
            nodes=tuple(self.components),
            connections=tuple(
                TopologyConnection(
                    edge_id=item.connection_id,
                    source=item.source_component_id,
                    target=item.target_component_id,
                    service=item.service,
                    enabled=True,
                )
                for item in self.connections.values()
            ),
        )

    def compile_execution_plan(self) -> AuthoringExecutionPlan:
        """Compile authored ports into storage boundaries and flow nodes."""

        if not self.components:
            raise ValueError("facility draft must contain at least one component")

        connection_ports: dict[str, list[str]] = {key: [] for key in self.connections}
        for connection in self.connections.values():
            connection_ports[connection.connection_id].extend((
                f"{connection.source_component_id}.{connection.source_port_id}",
                f"{connection.target_component_id}.{connection.target_port_id}",
            ))

        nodes: list[AuthoringExecutionNode] = []
        port_nodes: dict[tuple[str, str], str] = {}
        issues: list[str] = []
        for connection_id, refs in sorted(connection_ports.items()):
            storage_refs = [
                ref for ref in refs
                if self.components[ref.split(".", 1)[0]].component_model in _STORAGE_MODELS
            ]
            if len(storage_refs) > 1:
                issues.append(
                    f"connection {connection_id} joins multiple storage boundaries: "
                    + ", ".join(storage_refs)
                )
            node_kind = "storage_boundary" if storage_refs else "junction"
            node_id = f"NODE::{connection_id}"
            nodes.append(AuthoringExecutionNode(node_id, node_kind, tuple(refs)))
            for ref in refs:
                component_id, port_id = ref.split(".", 1)
                port_nodes[(component_id, port_id)] = node_id

        exposed: list[str] = []
        for component_id, component in self.components.items():
            for port in component.ports:
                key = (component_id, port.port_id)
                if key in port_nodes:
                    continue
                ref = f"{component_id}.{port.port_id}"
                node_id = f"EXTERNAL::{ref}"
                nodes.append(AuthoringExecutionNode(node_id, "exposed_boundary", (ref,)))
                port_nodes[key] = node_id
                exposed.append(ref)

        execution_components: list[AuthoringExecutionComponent] = []
        for component_id, component in self.components.items():
            if component.component_model in _STORAGE_MODELS:
                role = "storage"
            elif component.component_model in _DIRECT_NETWORK_LINK_MODELS:
                role = "direct_network_link"
            elif component.component_model in _ADAPTER_REQUIRED_MODELS:
                role = "adapter_required"
            else:
                role = "unsupported"
                issues.append(
                    f"component {component_id} has no execution role for "
                    f"{component.component_model}"
                )
            if role != "storage" and len(component.ports) != 2:
                issues.append(f"component {component_id} must expose exactly two ports")
            execution_components.append(AuthoringExecutionComponent(
                instance_id=component_id,
                component_model=component.component_model,
                execution_role=role,
                port_nodes={
                    port.port_id: port_nodes[(component_id, port.port_id)]
                    for port in component.ports
                },
            ))

        return AuthoringExecutionPlan(
            design_id=self.design_id,
            nodes=tuple(nodes),
            components=tuple(execution_components),
            exposed_port_refs=tuple(sorted(exposed)),
            issues=tuple(issues),
        )

    def as_dict(self) -> dict[str, Any]:
        audit = self.audit()
        return {
            "schema_version": "0.1",
            "design_id": self.design_id,
            "components": [
                {
                    "instance_id": item.instance_id,
                    "template_id": item.template_id,
                    "component_model": item.component_model,
                    "capacity_scale": item.capacity_scale,
                    "model_parameters": deepcopy(dict(item.model_parameters)),
                    "parameter_overrides": deepcopy(dict(item.parameter_overrides)),
                    "parameter_source": item.parameter_source,
                    "ports": [
                        {
                            "port_id": port.port_id,
                            "medium": port.medium,
                            "direction": port.direction,
                            "role": port.role,
                        }
                        for port in item.ports
                    ],
                }
                for item in self.components.values()
            ],
            "connections": [
                {
                    "connection_id": item.connection_id,
                    "source": {
                        "component_id": item.source_component_id,
                        "port_id": item.source_port_id,
                    },
                    "target": {
                        "component_id": item.target_component_id,
                        "port_id": item.target_port_id,
                    },
                    "service": item.service,
                }
                for item in self.connections.values()
            ],
            "audit": {
                "component_count": audit.component_count,
                "connection_count": audit.connection_count,
                "ready_component_count": audit.ready_component_count,
                "invalid_component_ids": list(audit.invalid_component_ids),
                "isolated_component_ids": list(audit.isolated_component_ids),
                "numerically_buildable": audit.numerically_buildable,
            },
        }

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        library: ComponentLibrary,
    ) -> "FacilityDraft":
        if payload.get("schema_version") != "0.1":
            raise ValueError("unsupported facility-draft schema")
        draft = cls(str(payload.get("design_id", "")))
        raw_components = payload.get("components")
        if not isinstance(raw_components, list):
            raise ValueError("facility draft components must be a list")
        for raw in raw_components:
            component = draft.place_component(
                library,
                str(raw.get("template_id", "")),
                str(raw.get("instance_id", "")),
                capacity_scale=raw.get("capacity_scale", 1.0),
                parameter_overrides=raw.get("parameter_overrides", {}),
            )
            if raw.get("component_model") != component.component_model:
                raise ValueError(f"saved component model mismatch: {component.instance_id}")
            if raw.get("model_parameters") != dict(component.model_parameters):
                raise ValueError(f"saved component parameters mismatch: {component.instance_id}")

        raw_connections = payload.get("connections")
        if not isinstance(raw_connections, list):
            raise ValueError("facility draft connections must be a list")
        for raw in raw_connections:
            source = raw.get("source", {})
            target = raw.get("target", {})
            draft.connect(
                str(raw.get("connection_id", "")),
                str(source.get("component_id", "")),
                str(source.get("port_id", "")),
                str(target.get("component_id", "")),
                str(target.get("port_id", "")),
                service=str(raw.get("service", "")),
            )
        return draft


def load_facility_draft(
    path: str | Path,
    library: ComponentLibrary,
) -> FacilityDraft:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("facility draft root must be an object")
    return FacilityDraft.from_dict(payload, library)
