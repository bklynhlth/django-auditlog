import contextlib
import time
from contextvars import ContextVar
from functools import partial

from django.contrib.auth import get_user_model
from django.db.models.signals import pre_save

from auditlog.models import LogEntry

auditlog_value = ContextVar("auditlog_value")
auditlog_disabled = ContextVar("auditlog_disabled", default=False)

@contextlib.contextmanager
def set_source(source, remote_addr=None, remote_port=None):
    """Connect a signal receiver with current user attached."""
    # Initialize thread local storage
    context_data = {
        "signal_duid": ("set_source", time.time()),
        "remote_addr": remote_addr,
        "remote_port": remote_port,
    }
    auditlog_value.set(context_data)

    # Connect signal for automatic logging
    set_source = partial(
        _set_source,
        user=source,
        signal_duid=context_data["signal_duid"],
    )
    pre_save.connect(
        set_source,
        sender=LogEntry,
        dispatch_uid=context_data["signal_duid"],
        weak=False,
    )

    try:
        yield
    finally:
        try:
            auditlog = auditlog_value.get()
        except LookupError:
            pass
        else:
            pre_save.disconnect(sender=LogEntry, dispatch_uid=auditlog["signal_duid"])


def _set_source(user, sender, instance, signal_duid, **kwargs):
    """Signal receiver with extra 'user' and 'signal_duid' kwargs.

    This function becomes a valid signal receiver when it is curried with the source and a dispatch id.
    """
    try:
        auditlog = auditlog_value.get()
    except LookupError:
        pass
    else:
        if signal_duid != auditlog["signal_duid"]:
            return
        auth_user_model = get_user_model()
        if (
            sender == LogEntry
            and isinstance(user, auth_user_model)
            and instance.source is None
        ):
            instance.source = user
            instance.source_email = getattr(user, "email", None)

        instance.remote_addr = auditlog["remote_addr"]
        instance.remote_port = auditlog["remote_port"]


@contextlib.contextmanager
def disable_auditlog():
    token = auditlog_disabled.set(True)
    try:
        yield
    finally:
        try:
            auditlog_disabled.reset(token)
        except LookupError:
            pass
