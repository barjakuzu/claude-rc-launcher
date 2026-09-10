"""Outbound notifications for guard.py findings (task nt).

No notification transport lives in this repo. The user's chat/paging
integration is private, host-local configuration, never code: this module
only ever shells out to whatever command name is in the RC_NOTIFY_CMD
environment variable, and hands it ONE compact JSON object on stdin. Unset
RC_NOTIFY_CMD means notifications are off -- a normal, quiet state, not an
error -- which is what keeps a public repo free of anyone's chat ids,
webhook URLs, or bot tokens.

This module never imports store at module scope beyond what the type
hints in docstrings describe -- notify_new_findings() takes a store
instance as a plain argument, the same shape guard.py takes a snapshot,
so it stays unit-testable against a fake with a claim_notifications()
method instead of a real hub.db.

Same message discipline as guard.py's findings: rule, severity, target
identity (device/session id and display name), the human message, and the
numeric value/threshold. Never a session's prompt, cwd, working directory,
or any transcript fragment -- guard.py never put one in a finding to begin
with, so _compact_finding's explicit allowlist (rather than "everything
except a blocklist") is what keeps a future finding field from leaking out
through this module by accident.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time

_LOG = logging.getLogger(__name__)

# Same ranking guard.py uses (evaluate()'s _SEVERITY_RANK): lower is more
# severe. A configured minimum severity notifies at that rank AND every
# rank below it (more severe), never the reverse.
SEVERITY_RANK = {"alert": 0, "warn": 1}

DEFAULT_MIN_SEVERITY = "alert"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_COOLDOWN_SECONDS = 3600.0


def _env_float(name, default):
    """A positive float from an env var, or `default` for anything else
    (unset, empty, unparseable, zero, negative) -- a bad or missing value
    degrades to the documented default rather than raising or silently
    producing a zero/negative timeout or cooldown, which downstream would
    mean "never wait" / "never suppress a repeat" instead of "use the
    normal setting"."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def notify_cmd():
    """The configured external command, or "" when notifications are off.
    Whitespace-only counts as unset -- an operator's env file with
    `RC_NOTIFY_CMD=   ` should behave exactly like an absent one, not
    attempt to run an empty argv."""
    return os.environ.get("RC_NOTIFY_CMD", "").strip()


def min_severity():
    """RC_NOTIFY_MIN_SEVERITY, defaulting to "alert" (never "warn" by
    default -- see module docstring: a fleet with a single 25-hour-old
    session would otherwise page every day forever). An unrecognized
    value falls back to the default rather than silently matching
    nothing."""
    value = os.environ.get("RC_NOTIFY_MIN_SEVERITY", DEFAULT_MIN_SEVERITY).strip().lower()
    return value if value in SEVERITY_RANK else DEFAULT_MIN_SEVERITY


def timeout_seconds():
    return _env_float("RC_NOTIFY_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)


def cooldown_seconds():
    return _env_float("RC_NOTIFY_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS)


def _severity_passes(severity, threshold):
    return SEVERITY_RANK.get(severity, 99) <= SEVERITY_RANK.get(threshold, 0)


def _finding_key(f):
    """Same (device_id, session_id, rule) shape store.replace_alerts and
    store.claim_notifications both key on; session_id normalized to ""
    for a device-targeted finding, matching that same convention."""
    return (f.get("device_id"), f.get("session_id") or "", f.get("rule"))


def _compact_finding(f):
    """Explicit allowlist of the fields that leave this box in a
    notification -- see module docstring. Deliberately does not just
    forward the finding dict as-is: a future field added to
    guard.py's findings must be reviewed before it can reach an outbound
    command, not leak out by omission."""
    return {
        "rule": f.get("rule"),
        "severity": f.get("severity"),
        "target_type": f.get("target_type"),
        "device_id": f.get("device_id"),
        "session_id": f.get("session_id") or None,
        "name": f.get("name"),
        "message": f.get("message"),
        "value": f.get("value"),
        "threshold": f.get("threshold"),
    }


def build_event(findings):
    """One compact JSON-safe dict for a batch of findings -- always a
    single grouped event, even for a batch of one, so the receiving
    command has exactly one shape to handle."""
    compact = [_compact_finding(f) for f in findings]
    return {"type": "rc_guard_findings", "count": len(compact), "findings": compact}


def _run_cmd(cmd, payload_bytes, timeout):
    """Runs `cmd` (a shell-syntax string, split with shlex -- no shell
    interpolation of the payload itself, which travels over stdin, never
    as an argv token or an interpolated string) with `payload_bytes` on
    stdin and a hard timeout. Returns True only on a clean, on-time exit;
    False for anything else. Never raises: a hanging or crashing notify
    command is this repo's problem to survive, not the caller's problem
    to catch."""
    try:
        args = shlex.split(cmd)
    except ValueError:
        _LOG.warning("notify: RC_NOTIFY_CMD is not valid shell syntax, notification dropped")
        return False
    if not args:
        return False
    try:
        result = subprocess.run(
            args, input=payload_bytes, timeout=timeout,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        _LOG.warning("notify: RC_NOTIFY_CMD timed out after %.1fs, notification dropped", timeout)
        return False
    except Exception:
        _LOG.exception("notify: RC_NOTIFY_CMD failed to run, notification dropped")
        return False


def send(findings, cmd=None, timeout=None):
    """Send one grouped notification for `findings` (already filtered and
    deduplicated by the caller -- this function does not consult severity
    or the cooldown floor itself). No-op, quietly, when RC_NOTIFY_CMD is
    unset and `cmd` was not passed explicitly. Never raises."""
    try:
        if not findings:
            return False
        cmd = cmd if cmd is not None else notify_cmd()
        if not cmd:
            return False
        timeout = timeout if timeout is not None else timeout_seconds()
        payload = json.dumps(build_event(findings)).encode("utf-8")
        return _run_cmd(cmd, payload, timeout)
    except Exception:
        _LOG.exception("notify: send() failed unexpectedly")
        return False


def notify_new_findings(new_findings, store, now_fn=time.time):
    """The poll loop's entry point, called once per cycle right after
    store.replace_alerts() with that call's own "new" list -- findings
    whose (device_id, session_id, rule) key was not already present in
    the alerts table, i.e. ones that just started firing. That is what
    makes "first fire only" work without a second store read: a finding
    that has been open for hours was never in `new_findings` to begin
    with, on every cycle after its first.

    Filters by the configured severity threshold, then claims each
    survivor against store.claim_notifications()'s cooldown floor
    (persisted, so a hub restart or a flapping alert cannot bypass it),
    and sends whatever is left as ONE grouped message -- a burst of
    several findings in one poll cycle is one notify command invocation,
    not one per finding.

    Does nothing at all, not even a store write, when RC_NOTIFY_CMD is
    unset: an idle install (the common case -- most users never set this)
    must not pay for a notify_log write it will never use, and must never
    surface an error for simply not having configured notifications.

    Never raises: this runs inside fleetpoll's poll loop, and a bug or a
    store error here must not be the reason devices stop being polled."""
    try:
        if not new_findings:
            return
        cmd = notify_cmd()
        if not cmd:
            return

        threshold = min_severity()
        candidates = [f for f in new_findings if _severity_passes(f.get("severity"), threshold)]
        if not candidates:
            return

        cooldown = cooldown_seconds()
        keys = [_finding_key(f) for f in candidates]
        try:
            claimed = set(store.claim_notifications(keys, cooldown, now_fn=now_fn))
        except Exception:
            _LOG.exception("notify: cooldown check failed, skipping this cycle's notification")
            return

        to_send = [f for f in candidates if _finding_key(f) in claimed]
        if not to_send:
            return
        send(to_send, cmd=cmd)
    except Exception:
        _LOG.exception("notify: notify_new_findings failed unexpectedly")
