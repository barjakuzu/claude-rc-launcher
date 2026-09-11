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
import guard
import noredirect
import notify
import store

# Imported lazily inside _ingest (not at module load) to avoid a hard
# circular-import dependency: server.py is the much heavier module and
# nothing in it imports fleetpoll, but keeping the import inside the
# function makes that non-cycle explicit and cheap to change later.

_LOG = logging.getLogger(__name__)

BACKOFF_CEILING_SECONDS = 300

# store.Store.prune() deletes ended sessions/events/audit rows older than
# its `days` cutoff. Nothing ever called it before, so the sessions table
# grew forever; call it from the poll loop, but no more than this often --
# it's a maintenance sweep, not part of every 30s cycle.
PRUNE_INTERVAL_SECONDS = 3600

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
    # Task L5 fix round 2: every request this function makes IS a hub
    # polling a device as part of a fleet (it is the only caller of this
    # function). Marks it as such so the device's own fleet.is_limits_hub()
    # can tell "I am being polled by a hub" apart from "nobody polls me"
    # without inferring it from credentials or a remote address -- see
    # fleet.py's HUB_POLL_HEADER/note_hub_poll for the receiving side.
    req.add_header(fleet.HUB_POLL_HEADER, "1")
    if auth_user or auth_pass:
        tok = base64.b64encode(f"{auth_user}:{auth_pass}".encode()).decode()
        req.add_header("Authorization", f"Basic {tok}")
    try:
        # Fix round 2: noredirect.NO_REDIRECT_OPENER, never bare
        # urllib.request.urlopen -- urlopen's default opener follows a
        # redirect and RE-SENDS the Authorization header (this hub's own
        # Basic auth password for this device, straight out of
        # devices.json) to wherever the redirect points. A device
        # answering with a 302 could otherwise have this password
        # forwarded anywhere, over plaintext http included. See
        # noredirect.py.
        with noredirect.NO_REDIRECT_OPENER.open(req, timeout=timeout) as r:
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
        self._next_prune_at = 0.0

    def _ingest(self, device_id, snapshot):
        import server  # lazy import, see note above

        # CONTRACT.md section 1: usage_meta.partial says this poll's usage
        # numbers may be LOW (the device's usage cache hasn't converged
        # yet, e.g. right after a restart), not that they're missing.
        # Recorded on the device row (store.py's additive usage_partial
        # column) because that is the only thing both server.py's
        # /api/fleet route and this poller's own guard step (see
        # _run_guard) can reach from just a Store handle -- neither holds
        # a reference to the other.
        usage_meta = snapshot.get("usage_meta")
        usage_partial = bool(usage_meta.get("partial")) if isinstance(usage_meta, dict) else False

        self.store.upsert_device({
            "id": device_id, "name": snapshot.get("device_name", device_id),
            "role": snapshot.get("role", "full"), "version": snapshot.get("version"),
            "claude_version": snapshot.get("claude_version"),
            "usage_partial": usage_partial,
        })
        sessions_in = snapshot.get("sessions") or []
        for s in sessions_in:
            if not isinstance(s, dict):
                continue
            # Fix round 1 (Important): sessions.list_rc_sessions() emits
            # `workdir` (+ a basename-only `project`, a different thing)
            # for a launcher row and never `cwd` -- only an external row
            # carries `cwd`. Without this, every launcher session's stored
            # `cwd` column is NULL, which makes store._encode_cwd_as_project
            # -- used by cost_view().sessions -- report project="" for
            # every launcher session regardless of its real project.
            # setdefault, not assignment: never overwrite an external
            # row's real `cwd` with a `workdir` it doesn't have.
            s.setdefault("cwd", s.get("workdir"))
            # CONTROLLER RULING: needs_attention is derived from each
            # session's own polled state (waiting_for/blocked/busy/idle),
            # not from Stop/UserPromptSubmit events which no longer exist.
            # Compute and store it here so server.py's /api/fleet route
            # can read it straight off the store row.
            try:
                s["state"] = server._derive_session_state(s)
            except Exception:
                pass
        # store.ENDED_ROW_GRACE_SECONDS is a floor, not the effective
        # value -- couple it explicitly to this poller's own interval so a
        # longer-than-default interval (a single missed poll at that
        # interval) can't silently disable the flicker-vs-new-session
        # grace window in upsert_sessions.
        grace_seconds = max(store.ENDED_ROW_GRACE_SECONDS, 3 * self.interval)
        self.store.upsert_sessions(device_id, sessions_in, grace_seconds=grace_seconds)

        # Phase 3 wiring (CONTRACT.md sections 1/2): persist per-session
        # usage and per-project daily cost totals. Each block is its own
        # try/except, deliberately separate from the sessions/events
        # ingest above and from each other -- a malformed usage or cost
        # shape from THIS device must not prevent its sessions/events from
        # being ingested, its cursor from advancing, or the OTHER of these
        # two writes from happening. store.py's own coercion already
        # drops a bad row without raising for the common cases (a
        # non-numeric field, a missing key); these try/excepts are the
        # backstop for anything store.py can't anticipate (snapshot["sessions"]
        # not actually a list of dicts, say) so this poller's own
        # "never breaks the loop" guarantee holds even then.
        try:
            usage_rows = []
            for s in sessions_in:
                if not isinstance(s, dict):
                    continue
                u = s.get("usage")
                sid = s.get("session_id")
                if not isinstance(u, dict) or not sid:
                    continue
                usage_rows.append({
                    "session_id": sid,
                    "input": u.get("input"), "cache_read": u.get("cache_read"),
                    "cache_write": u.get("cache_write"), "output": u.get("output"),
                    "effective": u.get("effective"), "last_ts": u.get("last_ts"),
                })
            if usage_rows:
                self.store.upsert_session_usage(device_id, usage_rows)
        except Exception:
            _LOG.exception("fleetpoll: session usage ingest failed for device %r", device_id)

        try:
            by_project = snapshot.get("usage_daily_by_project")
            if isinstance(by_project, list):
                cost_rows = [
                    {"day": r.get("day"), "project": r.get("project"),
                     "input": r.get("input"), "cache_read": r.get("cache_read"),
                     "cache_write": r.get("cache_write"), "output": r.get("output"),
                     "effective": r.get("effective")}
                    for r in by_project if isinstance(r, dict)
                ]
            else:
                # A metadata-role device omits usage_daily_by_project
                # entirely (CONTRACT.md: a project name is the cwd by
                # another name), and a pre-fleet legacy device (see
                # _poll_remote_legacy) never had either key to begin with.
                # Either way, fall back to the per-day (not per-project)
                # totals with project="" so the device still contributes
                # to /api/cost's per-device totals -- it just can never
                # appear in the projects breakdown.
                daily = snapshot.get("usage_daily")
                cost_rows = [
                    {"day": r.get("day"), "project": "",
                     "input": r.get("input"), "cache_read": r.get("cache_read"),
                     "cache_write": r.get("cache_write"), "output": r.get("output"),
                     "effective": r.get("effective")}
                    for r in (daily if isinstance(daily, list) else []) if isinstance(r, dict)
                ]
            if cost_rows:
                # Task lg: stamps this poll's usage_partial onto every row
                # touched this call -- store.Store.upsert_cost_daily's own
                # docstring covers why that's a per-row fact, not a
                # device-wide one, and effective_tokens_in_daily_window
                # reads it back.
                self.store.upsert_cost_daily(device_id, cost_rows, partial=usage_partial)
        except Exception:
            _LOG.exception("fleetpoll: cost ingest failed for device %r", device_id)

        # Task-tk: hourly counterpart of the cost_daily ingest above, for
        # store.Store.effective_tokens_in_hourly_window's five-hour
        # figure. Unlike usage_daily_by_project, usage_hourly is present
        # (kept-but-reduced, never dropped) under every role -- see
        # fleet.py's own reasoning -- so there is no metadata fallback
        # branch to mirror here; a legacy pre-fleet device's synthesized
        # snapshot (see _poll_remote_legacy) simply has no "usage_hourly"
        # key at all, which `isinstance(hourly, list)` below already
        # treats as "nothing to ingest this poll", not an error. Its own
        # try/except, same reasoning as every other ingest step here: a
        # malformed usage_hourly shape from THIS device must never
        # prevent its sessions/events/usage/cost-daily/limits from being
        # ingested or its cursor from advancing.
        try:
            hourly = snapshot.get("usage_hourly")
            if isinstance(hourly, list):
                hourly_rows = [
                    {"hour": r.get("hour"),
                     "input": r.get("input"), "cache_read": r.get("cache_read"),
                     "cache_write": r.get("cache_write"), "output": r.get("output"),
                     "effective": r.get("effective")}
                    for r in hourly if isinstance(r, dict)
                ]
                if hourly_rows:
                    # Task lg: same per-row partial stamp as upsert_cost_daily
                    # above -- see store.Store.upsert_cost_hourly's docstring.
                    self.store.upsert_cost_hourly(device_id, hourly_rows, partial=usage_partial)
        except Exception:
            _LOG.exception("fleetpoll: hourly cost ingest failed for device %r", device_id)

        # CONTRACT.md sections 3-4: persist this device's account-limits
        # reading whole, as reported. A legacy pre-fleet device's
        # synthesized snapshot (see _poll_remote_legacy) never has a
        # "limits" key at all, same as any current device that
        # fleet.is_limits_hub() has elected NOT to fetch this poll
        # (fleet.py: "limits_result stays None ... key omitted").
        #
        # Task L5 live-bug fix: those two cases used to be handled
        # identically -- a no-op, on the reasoning that there is nothing
        # to store and no reason to write a fabricated unavailable row
        # over whatever a later real poll eventually reports. That is
        # still correct for a device that has NEVER reported limits (a
        # legacy device, or one that has simply never been elected
        # fetcher), but it left a real bug for a device that USED to be
        # this account's fetcher and became a satellite: its last real
        # row stayed in account_limits forever, getting more stale every
        # poll, with nothing here ever telling the store it was no longer
        # current -- confirmed live: a satellite's 35-hour-old row still
        # being compared against the hub's fresh one by limits_view()'s
        # divergence check.
        #
        # The fix: an ABSENT "limits" key on a snapshot that otherwise
        # ingested successfully (we are past the isinstance(snapshot,
        # dict) checks above the cost/hourly ingest steps this sits
        # alongside) is not just "nothing to add", it is fleet.py's own
        # is_limits_hub() telling us, as of THIS poll, "not my job right
        # now" -- a live, positive statement about the current fleet
        # role, from the one device that actually knows it. Clearing this
        # device's row on that signal is what keeps account_limits
        # honest instead of accumulating rows for devices that stopped
        # updating them: a device that never had a row (the common case)
        # gets a harmless no-op DELETE; a device that just became a
        # satellite gets its now-stale row removed within one poll cycle
        # of its hub reaching it, exactly matching how quickly
        # fleet.is_limits_hub() itself flips a device into satellite mode.
        # store.limits_view()'s own ACCOUNT_LIMITS_STALE_SECONDS gate on
        # `divergent` is the backstop for the gap this can't close -- a
        # device that goes fully unreachable, so no poll (and therefore
        # no clear) ever happens again.
        #
        # Coordinator review (2026-09-11): a "limits" key that IS present
        # but malformed (not a dict -- upsert_account_limits's own
        # not-a-dict skip, e.g. a buggy remote device sending a string or
        # a list) is a different case from an absent key, and must go
        # back to being a no-op, not a clear. Treating them alike turned
        # one transient bad payload into permanent data loss for a device
        # that IS still this account's fetcher -- last-good is kept
        # specifically to survive exactly that kind of bad moment, and a
        # single malformed poll must not throw it away. Only a truly
        # absent key -- "limits" not present in the snapshot at all --
        # is the is_limits_hub() satellite signal the clear above exists
        # for; a present-but-wrong-shaped value is not that signal.
        #
        # Its own try/except, same as usage/cost above: a malformed
        # "limits" shape, or a failed upsert/clear, from THIS device must
        # never prevent its sessions/events/usage/cost from being
        # ingested or its cursor from advancing.
        try:
            if "limits" in snapshot:
                limits_payload = snapshot["limits"]
                if isinstance(limits_payload, dict):
                    self.store.upsert_account_limits(device_id, limits_payload)
                # else: present but malformed -- leave any existing
                # last-good row untouched, same as calling
                # upsert_account_limits directly with a non-dict payload
                # already does (it logs and skips, never deletes).
            else:
                self.store.clear_account_limits(device_id)
        except Exception:
            _LOG.exception("fleetpoll: limits ingest failed for device %r", device_id)

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
                self._run_guard(now)
                self._maybe_prune(now)
            except Exception:
                _LOG.exception("fleetpoll: poll_once failed unexpectedly")

    def _run_guard(self, now):
        """CONTRACT.md section 4: run guard.evaluate() once per completed
        poll cycle (every device has already been polled by the time this
        runs, so a guard failure here can never be the reason a device
        didn't get polled) and persist the result. Never raises: a guard
        or store failure here is this poller's own bug, not a symptom of
        any one device's payload, and must not prevent the next cycle
        from running."""
        try:
            snapshot = self.store.fleet_view(include_ended=False)
            sessions_in = snapshot.get("sessions")
            devices_in = snapshot.get("devices")
            sessions_in = sessions_in if isinstance(sessions_in, list) else []
            devices_in = devices_in if isinstance(devices_in, list) else []

            try:
                event_ts_map = self.store.last_event_ts_map()
            except Exception:
                _LOG.exception("fleetpoll: guard last_event_ts lookup failed")
                event_ts_map = {}

            partial_device_ids = {
                d.get("id") for d in devices_in
                if isinstance(d, dict) and d.get("usage_partial")
            }

            enriched_sessions = []
            for s in sessions_in:
                if not isinstance(s, dict):
                    continue
                # Never mutate fleet_view()'s own dicts in place -- other
                # callers in this same process (server.py's /api/fleet,
                # the SSE stream) may still be holding this exact list.
                s = dict(s)
                s["last_event_ts"] = event_ts_map.get((s.get("device_id"), s.get("session_id")))

                # Fix round 1 (Critical): store.upsert_sessions refreshes
                # `last_seen` to the real wall clock for EVERY reported
                # session on EVERY poll -- it is a liveness heartbeat
                # ("the hub saw this session in a poll"), not an activity
                # signal, so it is always approximately "now" and elapsed
                # time against it is always approximately zero.
                # guard._rule_stalled treats a session's `last_seen` as
                # one of its activity candidates; left as-is, that makes
                # the rule permanently dead in production, which silently
                # defeats the whole point of the status=="busy" correction
                # just below (the case the contract carved that out for).
                # Withheld on this local enrichment copy only -- no other
                # rule reads session `last_seen`, and the store row (and
                # therefore the UI) is untouched.
                s["last_seen"] = None

                # CONTRACT.md section 4, "the stalled rule needs the raw
                # status": _derive_session_state ranks needs_attention
                # above busy, so a session wedged inside a Stop hook stops
                # presenting as busy in the derived `state` the UI reads
                # and would escape the `stalled` rule, which exists
                # precisely to catch that case. `status` (persisted
                # verbatim from the device's own report) is the raw
                # signal the stalled rule actually needs -- so when the
                # raw status is "busy", guard is handed state="busy" here
                # regardless of what the derived state says. This is a
                # local copy used only for this evaluate() call: the
                # store row's own `state` column (and therefore the UI)
                # is untouched, so `state` (derived) and `status` (raw)
                # keep meaning two different things -- do not collapse
                # them back into one field.
                if s.get("status") == "busy":
                    s["state"] = "busy"

                # CONTRACT.md amendment, "guard must not fire cost rules
                # on partial data": after a daemon restart the device's
                # usage cache is cold for ~12 polls and EVERY session
                # under-reads during that window; usage_partial is the
                # only signal this is happening. Dropping `usage` (not
                # the session itself) makes guard.py's existing "missing
                # usage means cost rules can't run here" behavior do
                # exactly the right thing: age/stall/concurrency, which
                # don't read usage, still evaluate normally.
                # `projects_capped` is deliberately NOT checked here --
                # that flag means the totals are trustworthy and only the
                # per-project breakdown was truncated for size, which is
                # not a reason to withhold usage from a cost rule.
                if s.get("device_id") in partial_device_ids:
                    s["usage"] = None

                enriched_sessions.append(s)

            findings = guard.evaluate(
                {"devices": devices_in, "sessions": enriched_sessions},
                now_fn=lambda: now)
            result = self.store.replace_alerts(findings, now_fn=lambda: now)
        except Exception:
            _LOG.exception("fleetpoll: guard evaluation failed")
            return

        # notify.py (task nt): a finding notifies on the cycle it FIRST
        # appears in the alerts table, never on every cycle it keeps
        # firing -- store.replace_alerts's own "new" list is exactly that
        # set, computed from the same pre-insert snapshot the delete pass
        # already needed, so this can't drift from what actually got
        # persisted. Its own try/except, separate from the guard
        # evaluation above: a notify failure is not a guard failure, and
        # must never be logged or reasoned about as one. notify.py itself
        # already never raises and enforces its own subprocess timeout
        # (never blocking this loop), but this call runs after every
        # device this cycle has already been polled, so even a defensive
        # failure here changes nothing about device polling.
        try:
            notify.notify_new_findings(result.get("new", []), self.store, now_fn=lambda: now)
        except Exception:
            _LOG.exception("fleetpoll: notify failed")

    def _maybe_prune(self, now):
        if now < self._next_prune_at:
            return
        self._next_prune_at = now + PRUNE_INTERVAL_SECONDS
        try:
            result = self.store.prune()
            _LOG.info("fleetpoll: pruned old rows: %s", result)
        except Exception:
            _LOG.exception("fleetpoll: prune failed")

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
