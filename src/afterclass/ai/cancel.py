"""Stop a running Ask from the page (its Stop button).

The server runs each question under an id (`asking`); a provider registers how to abort its call in
flight (`abortable`: close the socket to the local model, kill the `claude` process). `cancel(id)` calls
that, and the provider's call then raises Cancelled.
"""

import threading
from contextlib import contextmanager

from ..ui import AfterclassError


class Cancelled(AfterclassError):
    pass


_lock = threading.Lock()
_asks = {}  # ask id -> {"abort": callable or None, "cancelled": bool}
_current = threading.local()  # the ask id of this (server handler) thread


@contextmanager
def asking(ask_id):
    """Run a question under ask_id, so cancel(ask_id) can stop it. A falsy id: not cancellable."""
    if not ask_id:
        yield
        return
    with _lock:
        _asks[ask_id] = {"abort": None, "cancelled": False}
    _current.id = ask_id
    try:
        yield
    finally:
        _current.id = None
        with _lock:
            _asks.pop(ask_id, None)


@contextmanager
def abortable(abort):
    """Inside a provider call: abort() stops it from another thread when this question is cancelled."""
    ask_id = getattr(_current, "id", None)
    with _lock:
        entry = _asks.get(ask_id)
        if entry is not None:
            if entry["cancelled"]:
                raise Cancelled("stopped")
            entry["abort"] = abort
    try:
        yield
    except Exception:
        if cancelled():
            raise Cancelled("stopped")
        raise
    finally:
        with _lock:
            if entry is not None:
                entry["abort"] = None
    if cancelled():
        raise Cancelled("stopped")


def cancelled():
    with _lock:
        entry = _asks.get(getattr(_current, "id", None))
        return bool(entry and entry["cancelled"])


def cancel(ask_id):
    """Stop the question ask_id. Returns True if it was running."""
    with _lock:
        entry = _asks.get(ask_id)
        if entry is None:
            return False
        entry["cancelled"] = True
        abort = entry["abort"]
    if abort:
        try:
            abort()
        except OSError:
            pass
    return True
