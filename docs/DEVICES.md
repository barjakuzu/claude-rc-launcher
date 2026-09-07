# Adding a device to the fleet

A device is any machine running this app's `app.py` (installed via
`install.sh`) that the hub can reach. The hub is never listed as a device
itself — it's the implicit "local" row.

## Adding one

1. On the new device, run `install.sh` and note its Tailscale (or other
   private-network) address and the port it listens on (`RC_PORT`,
   default `8200`).
2. On the hub, add an entry to `~/.claude-rc/devices.json`:

   ```json
   [
     {
       "id": "example-device",
       "name": "Example Device",
       "base_url": "http://example-device.example.ts.net:8200",
       "auth_user": "admin",
       "auth_pass": "a strong random password"
     }
   ]
   ```

   `devices.json` holds per-device credentials in clear text (they can't
   be hashed — the hub needs to send them outbound) and must stay
   `chmod 600`, which `install.sh`/`config.py` already enforce for the
   directory it lives in.
3. Merge `docs/hooks/settings.snippet.json` into the new device's
   `~/.claude/settings.json` (see `README.md`'s "Hook events" section) so
   its sessions' lifecycle events reach the fleet spool.
4. The hub's poller picks the device up automatically within its poll
   interval (30s by default); no restart is required on either side.

## The two roles

Set `RC_ROLE` in the device's environment (its `~/.claude-rc/env`, which
`install.sh`'s wrapper script sources before launching `app.py`):

- `full` (default): the device serves everything — sessions, terminal,
  key injection, transcripts, schedules, config reports. This is what
  every personal device should run.
- `metadata`: the device serves only `GET /fleet`, `/version`, `/stats`,
  `/config-report`. Every other route (`/start`, `/keys`, `/resize`,
  `/ws`, `/enable-rc`, `/preview`, `/stop*`, `/restart`, `/unstick`,
  `/schedules*`) returns `403`. Its `/fleet` response hashes every
  session's `session_id` (and drops `name` unless explicitly
  allowlisted — none are, by default) with `RC_HASH_SALT`, and never
  includes `cwd`, `tmux`, `claude`, or token counts. Use this for a
  device where you want fleet visibility (it's running, it has N
  sessions, they're idle/busy) without exposing what those sessions are
  or letting the hub control them — for example, a work laptop where
  full visibility or remote control may not be appropriate.

`RC_HASH_SALT` is generated automatically on first run into
`~/.claude-rc/env` (mode `0600`) if not already set; it must also be
exported into the environment Claude Code's hooks run in (the same
`~/.claude-rc/env` file, since `rc-hook` reads `RC_HASH_SALT` from its
own process environment, which on most setups is the shell/session
environment Claude Code inherited — if hooks run under a different
environment than the launcher process, set `RC_HASH_SALT` there too, to
the *same* value, or a metadata device's sessions won't hash
consistently between `/fleet`'s session list and its hook-derived event
stream).

## Tailscale ACL note

Devices should bind their listener to a Tailscale IP, not `0.0.0.0`
(`RC_HOST` in `config.py`), and the network ACL should restrict `:8200`
so only the hub node can reach it — any other tailnet member reaching a
device's `:8200` directly bypasses the hub's own access controls (login,
audit log). This is a network-layer requirement this app cannot enforce
in code; configure it in your Tailscale ACL policy.

## What a metadata device does and does not expose

| Surface | full | metadata |
|---|---|---|
| `GET /fleet` sessions | full detail | hashed `session_id`, `name`/`state`/`started_at`/`kind` only |
| `GET /fleet` events | full detail | `{ts, event}` only, no `extra` |
| Terminal (`/ws`, `/keys`, `/resize`) | yes | 403 |
| Transcript (`/preview`) | yes | 403 |
| Launch/stop (`/start`, `/stop*`, `/restart`, `/unstick`, `/enable-rc`) | yes | 403 |
| Schedules (`/schedules*`) | yes | 403 |
| `GET /version`, `/stats`, `/config-report` | yes | yes |

## What the hub stores (`hub.db`)

The hub keeps its own SQLite database at `~/.claude-rc/hub.db`
(`store.py`), created `0600` and never sent anywhere — it backs the
`/api/fleet` roll-up and the SSE stream, and it never leaves the box it
runs on. It holds four tables:

- `devices`: id, name, role, version, claude version, last-seen
  timestamp, online flag — one row per polled device, hub included.
- `sessions`: device id, session id, name, cwd, kind, state, started/
  ended/last-seen timestamps — the fleet's current view of every
  session on every device. A `metadata` device's rows never carry a
  real `name`, `cwd`, or token counts (see the redaction table above);
  its session id is hashed before it ever reaches this table.
- `session_events`: the per-session event history behind `needs_attention`
  and each session's `/api/sessions/<device>/<session>/events` feed.
- `audit_log`: one row per mutating hub route (start/stop/restart/
  rename/etc.) — actor, action, target, device, timestamp, detail.
  Readable in the app under Settings > Audit.

A single writer thread funnels all writes through one queue (SQLite/WAL
allows one writer at a time); reads use short-lived per-thread
connections against the file directly.

## The `/api/fleet/stream` SSE endpoint

`GET /api/fleet/stream` pushes the same payload as `GET /api/fleet`
(a `{devices, sessions}` snapshot) as an SSE `data:` frame every time
something changes, plus a `{"type":"heartbeat","ts":<epoch>}` data frame
every `SSE_HEARTBEAT_SECONDS` (20s) of quiet so the browser's
`EventSource.onmessage` fires even when nothing changed — this is what
lets the client tell "no changes" apart from "connection silently
died." The frontend (`useFleet.ts`) falls back to polling `GET
/api/fleet` every 5s whenever the stream is down, reconnecting, or has
gone quiet longer than ~2 heartbeat intervals, and drops back to
SSE-only once a fresh frame arrives. `/api/fleet/stream` caps concurrent
subscribers (`SSE_MAX_SUBSCRIBERS`); past the cap it returns `503` with
`Retry-After: 5` and the client is expected to poll `GET /api/fleet`
instead.

## The five hook events and `rc-hook`'s spool

Each device installs `hooks/rc-hook` as a Claude Code hook for five
events — `SessionEnd`, `StopFailure`, `Notification`, `SubagentStop`,
`PreCompact` (`SessionStart`/`UserPromptSubmit`/`Stop` are deliberately
*not* hooked here: the hub's poller already sees those transitions on
its own cadence, while the five hooked events fire in between polls or
explain things a poll only ever sees as a session vanishing).
`rc-hook` appends each event as one JSON line to
`~/.claude-rc/events/<YYYY-MM-DD>.jsonl`. A file is rotated to
`<date>.jsonl.1` (clobbering any previous `.1`) once it exceeds
`ROTATE_SIZE_BYTES` (8 MiB), and spool files older than
`RETENTION_DAYS` (7 days) are pruned on every hook invocation. Worst
case on disk: today's file (up to 8 MiB) + yesterday's rotated `.1`
file (up to 8 MiB) + up to 7 more days of `<date>.jsonl` files.
