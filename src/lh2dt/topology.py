"""Validation of the facility component graph before numerical assembly."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class TopologyConnection:
    edge_id: str
    source: str
    target: str
    service: str
    enabled: bool


@dataclass(frozen=True)
class FacilityTopology:
    schema_version: str
    resolution: str
    nodes: tuple[str, ...]
    connections: tuple[TopologyConnection, ...]


@dataclass(frozen=True)
class TopologyAudit:
    node_count: int
    connection_count: int
    enabled_connection_count: int
    connected_component_count: int
    disabled_edges: tuple[str, ...]
    isolated_nodes: tuple[str, ...]


def load_facility_topology(path: str | Path) -> FacilityTopology:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != "0.1":
        raise ValueError("Unsupported topology schema")
    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("Topology must contain a non-empty node list")
    nodes = tuple(str(value) for value in raw_nodes)
    if any(not value for value in nodes) or len(set(nodes)) != len(nodes):
        raise ValueError("Topology node names must be non-empty and unique")
    node_set = set(nodes)
    raw_connections = data.get("connections")
    if not isinstance(raw_connections, list):
        raise ValueError("Topology connections must be a list")
    connections: list[TopologyConnection] = []
    edge_ids: set[str] = set()
    for raw in raw_connections:
        edge_id = str(raw.get("edge_id", ""))
        source = str(raw.get("from", ""))
        target = str(raw.get("to", ""))
        service = str(raw.get("service", ""))
        if not edge_id or edge_id in edge_ids:
            raise ValueError("Topology edge IDs must be non-empty and unique")
        if source not in node_set or target not in node_set:
            raise ValueError(f"Topology edge {edge_id} has a missing endpoint")
        if source == target:
            raise ValueError(f"Topology edge {edge_id} cannot be a self-loop")
        if not service:
            raise ValueError(f"Topology edge {edge_id} requires a service description")
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"Topology edge {edge_id} enabled must be bool")
        edge_ids.add(edge_id)
        connections.append(TopologyConnection(
            edge_id=edge_id,
            source=source,
            target=target,
            service=service,
            enabled=enabled,
        ))
    return FacilityTopology(
        schema_version="0.1",
        resolution=str(data.get("resolution", "")),
        nodes=nodes,
        connections=tuple(connections),
    )


def audit_topology(topology: FacilityTopology) -> TopologyAudit:
    adjacency = {node: set() for node in topology.nodes}
    for edge in topology.connections:
        # Disabled alternatives still belong to the structural graph and must
        # remain connectable when a scenario enables them.
        adjacency[edge.source].add(edge.target)
        adjacency[edge.target].add(edge.source)
    isolated = tuple(sorted(node for node, peers in adjacency.items() if not peers))
    remaining = set(topology.nodes)
    components = 0
    while remaining:
        components += 1
        stack = [remaining.pop()]
        while stack:
            node = stack.pop()
            for peer in adjacency[node]:
                if peer in remaining:
                    remaining.remove(peer)
                    stack.append(peer)
    return TopologyAudit(
        node_count=len(topology.nodes),
        connection_count=len(topology.connections),
        enabled_connection_count=sum(edge.enabled for edge in topology.connections),
        connected_component_count=components,
        disabled_edges=tuple(
            edge.edge_id for edge in topology.connections if not edge.enabled
        ),
        isolated_nodes=isolated,
    )
