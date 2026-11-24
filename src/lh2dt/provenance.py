"""Separate provenance buckets for production model builds."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import asdict, is_dataclass
from typing import Any


@dataclass(frozen=True)
class ModelProvenance:
    """Traceability metadata kept separate from model numerical parameters."""

    asset_id: str
    component_model: str
    parameter_source: str
    external_evidence_ids: tuple[str, ...] = ()
    field_evidence_ids: tuple[str, ...] = ()
    fit_result_ids: tuple[str, ...] = ()
    production_model: bool = True
    parameter_fitting_or_inverse_estimation: bool = False

    def __post_init__(self) -> None:
        if not self.asset_id.strip():
            raise ValueError("asset_id must be non-empty")
        if not self.component_model.strip():
            raise ValueError("component_model must be non-empty")
        if not self.parameter_source.strip():
            raise ValueError("parameter_source must be non-empty")
        if self.production_model and self.parameter_fitting_or_inverse_estimation:
            raise ValueError("production model provenance cannot enable fitting")

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "component_model": self.component_model,
            "parameter_source": self.parameter_source,
            "external_evidence_ids": list(self.external_evidence_ids),
            "field_evidence_ids": list(self.field_evidence_ids),
            "fit_result_ids": list(self.fit_result_ids),
            "production_model": self.production_model,
            "parameter_fitting_or_inverse_estimation": self.parameter_fitting_or_inverse_estimation,
        }

    def attach_to_result(self, result: Any) -> dict[str, Any]:
        """Return a JSON-safe result envelope with separated provenance.

        The numerical result is copied; the original dataclass or mapping is
        never mutated.  This is the common boundary for dynamic tank, pipe,
        vaporizer, reliquefier, valve, and instrumentation result logs.
        """

        if is_dataclass(result) and not isinstance(result, type):
            payload = asdict(result)
        elif isinstance(result, dict):
            payload = dict(result)
        else:
            payload = {"result": result}
        payload["provenance"] = self.as_dict()
        return payload


__all__ = ["ModelProvenance"]
