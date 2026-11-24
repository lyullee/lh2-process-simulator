"""Optional thread-local timestep observer; does not change numerical equations."""
from contextvars import ContextVar
from contextlib import contextmanager

_observer = ContextVar("simulation_observer", default=None)

@contextmanager
def observe_steps(callback):
    token = _observer.set(callback)
    try:
        yield
    finally:
        _observer.reset(token)

def emit_step(step=None):
    callback = _observer.get()
    if callback is not None:
        callback(step)
