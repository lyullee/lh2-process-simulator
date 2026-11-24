"""Configurable alarm-count interlock for synthetic research scenarios."""

from __future__ import annotations

import math


class EmergencyShutdownInterlock:
    """Rising-edge alarm counter with a manual-reset isolation latch.

    ``trip_alarm_count`` is an explicit procedure/control input.  For the
    application-specific procedure it can be configured as ``2`` after the source
    document has been reviewed.  A continuously active alarm counts once; a
    new count requires the signal to return to ``False`` and rise again.  The
    interlock never infers event timing or maps a procedure step to a valve
    command time.
    """

    def __init__(self, trip_alarm_count: int, *, manual_reset_required: bool = True) -> None:
        if isinstance(trip_alarm_count, bool) or not isinstance(trip_alarm_count, int):
            raise ValueError("trip_alarm_count must be an integer")
        if trip_alarm_count < 1:
            raise ValueError("trip_alarm_count must be at least one")
        if not isinstance(manual_reset_required, bool):
            raise ValueError("manual_reset_required must be bool")
        self.trip_alarm_count = trip_alarm_count
        self.manual_reset_required = manual_reset_required
        self._alarm_active = False
        self._alarm_count = 0
        self._latched = False

    @property
    def alarm_count(self) -> int:
        """Number of distinct rising-edge alarm events observed."""

        return self._alarm_count

    @property
    def latched(self) -> bool:
        """Whether the isolation action is currently latched."""

        return self._latched

    def observe_alarm(self, alarm_active: bool) -> bool:
        """Consume one boolean alarm sample and return the latch state.

        Only a ``False`` → ``True`` transition increments the event count.  A
        latched interlock remains latched until :meth:`reset` is called.
        """

        if not isinstance(alarm_active, bool):
            raise ValueError("alarm_active must be bool")
        rising_edge = alarm_active and not self._alarm_active
        self._alarm_active = alarm_active
        if rising_edge and not self._latched:
            self._alarm_count += 1
            if self._alarm_count >= self.trip_alarm_count:
                self._latched = True
        return self._latched

    def apply_opening(self, commanded_opening: float) -> float:
        """Apply the isolation latch to a normalized valve command.

        A non-latched command is returned unchanged.  Once tripped, the
        command is physically forced closed (``0.0``) until reset.
        """

        if isinstance(commanded_opening, bool):
            raise ValueError("commanded_opening must be numeric, not bool")
        try:
            opening = float(commanded_opening)
        except (TypeError, ValueError) as exc:
            raise ValueError("commanded_opening must be numeric") from exc
        if not math.isfinite(opening) or not 0.0 <= opening <= 1.0:
            raise ValueError("commanded_opening must be finite and in [0, 1]")
        return 0.0 if self._latched else opening

    def reset(self, *, authorized: bool = False) -> None:
        """Clear the latch after an explicit reset authorization."""

        if not isinstance(authorized, bool):
            raise ValueError("authorized must be bool")
        if self.manual_reset_required and not authorized:
            raise PermissionError("manual reset authorization is required")
        self._alarm_active = False
        self._alarm_count = 0
        self._latched = False


__all__ = ["EmergencyShutdownInterlock"]
