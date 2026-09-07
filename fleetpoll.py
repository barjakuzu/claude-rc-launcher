"""Hub poller: every interval seconds, pulls fleet.build_fleet() from the
local device in-process (no HTTP) and from every registered device over
GET /rc/fleet?since=<cursor>, writing results into store.Store. Backs off
an unreachable device from `interval` up to a 5-minute ceiling.
"""
import base64
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import devices
import fleet

# Imported lazily inside _ingest (not at module load) to avoid a hard
# circular-import dependency: server.py is the much heavier module and
# nothing in it imports fleetpoll, but keeping the import inside the
# function makes that non-cycle explicit and cheap to change later.

_LOG = logging.getLogger(__name__)

BACKOFF_CEILING_SECONDS = 300

# A device response larger than this is rejected outright (treated as a
# device error, same as a connection failure) rather than read into memory
# in full -- a misbehaving or compromised device returning a huge body
# must not be able to OOM the hub just because the request itself didn't
# time out.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class FleetEndpointNotFound(Exception):
    """Raised by http_get when a device answers 404/501 for /rc/fleet --
    a pre-fleet device (e.g. 2.1.7) that doesn't expose that route yet.
    Distinguished from a plain transport failure so the poller can fall
    back to the legacy /rc/sessions + /rc/version endpoints instead of
    marking the device offline."""


def _default_http_get(base_url, path, auth_user="", auth_pass="", since=None, timeout=10):
    url = base_url.rstrip("/") + path
    if since:
        url += "?since=" + urllib.parse.quote(since)
    req = urllib.request.Request(url)
    if auth_user or auth_pass:
        tok = base64.b64encode(f"{auth_user}:{auth_pass}".encode()).decode()
        req.add_header("Authorization", f"Basic {tok}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as e:
        if e.code in (404, 501):
            raise FleetEndpointNotFound(f"{path} -> HTTP {e.code}") from e
        raise
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError(
            f"device response exceeded {MAX_RESPONSE_BYTES} bytes, rejected")
    return json.loads(data)


class FleetPoller:
    def __init__(self, store, interval=30, http_get=None, on_change=None):
        self.store = store
        self.interval = interval
        self.http_get = http_get or _default_http_get
        self.on_change = on_change
        self._lock = threading.Lock()
        self._cursors = {}       # device_id -> cursor
        self._next_try_at = {}   # device_id -> epoch seconds
        self._backoff = {}       # device_id -> current backoff seconds
        self._stop = threading.Event()
        self._thread = None
        self._legacy_logged = set()  # device_ids we've already logged the fallback for

    def _ingest(self, device_id, snapshot):
        import server  # lazy import, see note above

        self.store.upsert_device({
            "id": device_id, "name": snapshot.get("device_name", device_id),
            "role": snapshot.get("role", "full"), "version": snapshot.get("version"),
            "claude_version": snapshot.get("claude_version"),
        })
        sessions_in = snapshot.get("sessions") or []
        for s in sessions_in:
            # CONTROLLER RULING: needs_attention is derived from each
            # session's own polled state (waiting_for/blocked/busy/idle),
            # not from Stop/UserPromptSubmit events which no longer exist.
            # Compute and store it here so server.py's /api/fleet route
            # can read it straight off the store row.
            try:
                s["state"] = server._derive_session_state(s)
            except Exception:
                pass
        self.store.upsert_sessions(device_id, sessions_in)
        events = snapshot.get("events") or []
        if events:
            self.store.add_events(device_id, events)
        if snapshot.get("cursor"):
            self._cursors[device_id] = snapshot["cursor"]
        self._backoff.pop(device_id, None)
        self._next_try_at.pop(device_id, None)
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                _LOG.exception("fleetpoll: on_change callback raised")

    def _poll_local(self, now):
        try:
            snapshot = fleet.build_fleet(since=self._cursors.get("local"))
            self._ingest("local", snapshot)
        except Exception:
            _LOG.exception("fleetpoll: local build_fleet failed")
            # the local device is always "online" from the hub's own view

    def _poll_remote(self, device, now):
        device_id = device["id"]
        due_at = self._next_try_at.get(device_id, 0)
        if now < due_at:
            return
        try:
            snapshot = self.http_get(
                device["base_url"], "/rc/fleet",
                auth_user=device.get("auth_user", ""), auth_pass=device.get("auth_pass", ""),
                since=self._cursors.get(device_id))
        except FleetEndpointNotFound:
            # Pre-fleet device (e.g. 2.1.7): fall back to /rc/sessions
            # (+/rc/version for metadata) rather than treating this as a
            # transport failure -- it must not go offline or back off.
            if device_id not in self._legacy_logged:
                _LOG.info(
                    "fleetpoll: device %r has no /rc/fleet endpoint, "
                    "falling back to /rc/sessions", device_id)
                self._legacy_logged.add(device_id)
            self._poll_remote_legacy(device, now)
            return
        except Exception:
            # Transport failure (unreachable, timed out, oversized/garbage
            # response): back off and mark the device offline.
            _LOG.warning("fleetpoll: device %r unreachable", device_id, exc_info=True)
            backoff = min(self._backoff.get(device_id, self.interval) * 2, BACKOFF_CEILING_SECONDS)
            self._backoff[device_id] = backoff
            self._next_try_at[device_id] = now + backoff
            self._mark_unreachable(device_id, device)
            return
        try:
            self._ingest(device_id, snapshot)
        except Exception:
            # A store-side failure while ingesting a snapshot we DID
            # successfully fetch is not the same problem as the device
            # being unreachable: the device answered fine. Don't back off
            # or mark it offline for a hiccup on our end -- next poll
            # will just retry ingest on schedule.
            _LOG.exception(
                "fleetpoll: ingest failed for device %r (store error, not backing off)", device_id)

    def _poll_remote_legacy(self, device, now):
        """Synthesize a fleet snapshot for a pre-fleet device from its
        legacy /rc/sessions (+ /rc/version) endpoints, with no events.
        A transport failure fetching /rc/sessions itself is still a real
        offline device and does back off; a missing/failing /rc/version
        is not fatal since the fallback still has a device to report."""
        device_id = device["id"]
        auth_user = device.get("auth_user", "")
        auth_pass = device.get("auth_pass", "")
        try:
            sessions_resp = self.http_get(
                device["base_url"], "/rc/sessions",
                auth_user=auth_user, auth_pass=auth_pass)
        except Exception:
            _LOG.warning("fleetpoll: device %r unreachable (legacy /rc/sessions)",
                          device_id, exc_info=True)
            backoff = min(self._backoff.get(device_id, self.interval) * 2, BACKOFF_CEILING_SECONDS)
            self._backoff[device_id] = backoff
            self._next_try_at[device_id] = now + backoff
            self._mark_unreachable(device_id, device)
            return

        version_resp = {}
        try:
            version_resp = self.http_get(
                device["base_url"], "/rc/version",
                auth_user=auth_user, auth_pass=auth_pass) or {}
        except Exception:
            _LOG.info("fleetpoll: device %r legacy /rc/version fetch failed, "
                       "continuing without it", device_id)

        sessions = sessions_resp.get("sessions", []) if isinstance(sessions_resp, dict) else (sessions_resp or [])
        snapshot = {
            "device_name": device.get("name", device_id),
            "role": version_resp.get("role", "full") if isinstance(version_resp, dict) else "full",
            "version": version_resp.get("version") if isinstance(version_resp, dict) else None,
            "claude_version": version_resp.get("claude_version") if isinstance(version_resp, dict) else None,
            "sessions": sessions,
            "events": [],
            "cursor": None,
        }
        try:
            self._ingest(device_id, snapshot)
        except Exception:
            _LOG.exception(
                "fleetpoll: legacy ingest failed for device %r (store error, not backing off)",
                device_id)

    def _mark_unreachable(self, device_id, device):
        """On a transport failure, mark the device offline without
        clobbering its last-known name/role/version with placeholder
        "unknown"/None values -- only write a placeholder row if the
        device isn't in the store at all yet (e.g. it has never
        answered a single poll)."""
        try:
            known = any(d["id"] == device_id for d in self.store.fleet_view()["devices"])
        except Exception:
            known = False
        try:
            if not known:
                self.store.upsert_device({
                    "id": device_id, "name": device.get("name", device_id),
                    "role": "unknown", "version": None, "claude_version": None,
                })
            self.store.mark_device_offline(device_id)
        except Exception:
            _LOG.exception("fleetpoll: failed to mark device %r offline", device_id)

    def poll_once(self, now_fn=time.time):
        """One pass over every device. Never raises. Serialized: a second
        concurrent call waits for the first to finish rather than racing
        it (a slow remote device must not pile up duplicate fetches)."""
        with self._lock:
            try:
                now = now_fn()
                self._poll_local(now)
                for device in devices.load_devices():
                    self._poll_remote(device, now)
            except Exception:
                _LOG.exception("fleetpoll: poll_once failed unexpectedly")

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                _LOG.exception("fleetpoll: poll loop iteration failed")
            self._stop.wait(self.interval)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="fleetpoll")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
