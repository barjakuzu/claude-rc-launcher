"""Turns Claude Code transcript JSONL files (~/.claude/projects/<encoded
cwd>/<session-uuid>.jsonl) into per-session and per-day token rollups.

Incremental: a module-level cache keyed by file path remembers how far
each file has been read so a poll every 30s only parses newly appended
bytes, not the whole history. See rollup() for the read/cache contract.

Security note (public repo): this module must never return a decoded cwd,
username, home path, or prompt/response content. "project" is only ever
the already-encoded ~/.claude/projects/<...> directory name.
"""
from __future__ import annotations

import collections
import datetime
import json
import os
import threading
import time

# Fixed weights for the "effective token" score. Exported so every caller
# (fleet poll, UI, tests) shares one definition instead of re-deriving it.
WEIGHTS: dict[str, float] = {
    "input": 1.0,
    "cache_read": 0.1,
    "cache_write": 2.0,
    "output": 5.0,
}

DEFAULT_MAX_BYTES_PER_CALL = 64 * 1024 * 1024  # 64 MiB
DEFAULT_DAYS = 30

# Cap on the number of tracked files so a box with years of transcripts
# can't grow the process without bound. Least-recently-stat'ed entries are
# evicted first once this is exceeded.
MAX_CACHE_ENTRIES = 2000

# Module-level cache, keyed by absolute transcript file path. An
# OrderedDict so "least recently stat'ed" eviction is a cheap
# move_to_end() on every visit plus a popitem(last=False) when over cap.
# Guarded by _lock for every read and write, including by rollup()'s own
# aggregation pass, so a concurrent reader never sees a half-updated entry.
_cache = collections.OrderedDict()
_lock = threading.Lock()


def _as_int(value):
    """Coerce a raw usage field to int. Missing/non-int values (including
    bool, which is technically an int subclass but never a legitimate
    token count) count as 0. A corrupt line must never raise."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


def effective(input=0, cache_read=0, cache_write=0, output=0):
    """The weighted sum of the four token buckets, rounded to an int.
    Exported so callers never re-derive the weights themselves."""
    total = (
        _as_int(input) * WEIGHTS["input"]
        + _as_int(cache_read) * WEIGHTS["cache_read"]
        + _as_int(cache_write) * WEIGHTS["cache_write"]
        + _as_int(output) * WEIGHTS["output"]
    )
    return int(round(total))


def _default_root():
    """~/.claude/projects, with CLAUDE_CONFIG_DIR overriding the
    "~/.claude" part when set, matching how Claude Code itself resolves
    its config directory."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        return os.path.join(os.path.expanduser(config_dir), "projects")
    return os.path.expanduser("~/.claude/projects")


def _parse_timestamp(ts):
    """ISO-8601 timestamp (with a trailing "Z", which Python 3.9's
    datetime.fromisoformat does not accept) to epoch seconds. Any
    unparsable or missing value returns None; callers treat that as "no
    timestamp" rather than raising."""
    if not isinstance(ts, str) or not ts:
        return None
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def _discover_files(root):
    """(entries, skipped) for every *.jsonl under root, newest-mtime-first.
    entries are (path, project, size, mtime, inode) tuples. `project` is
    the immediate parent directory name (the encoded, already-lossy cwd
    Claude Code uses, never decoded here). Files that fail os.stat (raced
    away, permission denied on the directory entry) count toward
    `skipped` rather than raising. A missing root simply yields nothing,
    since os.walk silently ignores a root it can't list."""
    entries = []
    skipped = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        project = os.path.basename(dirpath)
        for fname in filenames:
            if not fname.endswith(".jsonl"):
                continue
            path = os.path.join(dirpath, fname)
            try:
                st = os.stat(path)
            except OSError:
                skipped += 1
                continue
            entries.append((path, project, st.st_size, st.st_mtime, st.st_ino))
    entries.sort(key=lambda e: e[3], reverse=True)
    return entries, skipped


def _new_entry(project, session_id, inode):
    return {
        "inode": inode,
        "size": 0,
        "mtime": 0.0,
        "offset": 0,
        "seen_keys": set(),
        "totals": {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0},
        "first_ts": None,
        "last_ts": None,
        "session_id": session_id,
        "project": project,
        "models": {},
        "messages": 0,
        # Per-file daily buckets, date string -> raw totals dict. Kept
        # here (not recomputed from scratch) because the file may have
        # already scrolled out of the cache's read window by the time a
        # later rollup() call re-sums across files.
        "daily": {},
    }


def _read_new_bytes(path, offset):
    """Read a transcript file from `offset` to EOF. Its own function
    (rather than inlined) purely so tests can make one specific path fail
    to open/read (simulating a permission error or a file that vanished
    mid-read) without depending on real filesystem permission bits, which
    root ignores."""
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read()


def _consume_line(entry, raw_line):
    """Parse one complete transcript line (trailing newline included) and
    fold it into `entry` if it is a usage-bearing, non-duplicate,
    non-synthetic record. Never raises: a corrupt line, a line with no
    usage, or one with non-int usage values is simply skipped/zeroed."""
    try:
        row = json.loads(raw_line.decode("utf-8", errors="replace"))
    except ValueError:
        return
    if not isinstance(row, dict):
        return
    message = row.get("message")
    if not isinstance(message, dict):
        return
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return
    model = message.get("model")
    if model == "<synthetic>":
        return  # API error placeholder, not billable

    msg_id = message.get("id")
    row_uuid = row.get("uuid")
    if isinstance(msg_id, str) and msg_id:
        ident = msg_id
    elif isinstance(row_uuid, str) and row_uuid:
        ident = row_uuid
    else:
        ident = None  # neither present: count unconditionally, no dedup possible

    if ident is not None:
        key = (ident, row.get("requestId"))
        if key in entry["seen_keys"]:
            return
        entry["seen_keys"].add(key)

    # Only the four top-level usage counters. NEVER descend into
    # usage["iterations"]: those are per-iteration components of the same
    # cumulative total already reflected here, and double count if summed.
    input_i = _as_int(usage.get("input_tokens"))
    cache_read_i = _as_int(usage.get("cache_read_input_tokens"))
    cache_write_i = _as_int(usage.get("cache_creation_input_tokens"))
    output_i = _as_int(usage.get("output_tokens"))

    totals = entry["totals"]
    totals["input"] += input_i
    totals["cache_read"] += cache_read_i
    totals["cache_write"] += cache_write_i
    totals["output"] += output_i
    entry["messages"] += 1
    if isinstance(model, str) and model:
        entry["models"][model] = entry["models"].get(model, 0) + 1

    ts = _parse_timestamp(row.get("timestamp"))
    if ts is not None:
        if entry["first_ts"] is None or ts < entry["first_ts"]:
            entry["first_ts"] = ts
        if entry["last_ts"] is None or ts > entry["last_ts"]:
            entry["last_ts"] = ts
        date_str = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date().isoformat()
        day = entry["daily"].setdefault(
            date_str, {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0})
        day["input"] += input_i
        day["cache_read"] += cache_read_i
        day["cache_write"] += cache_write_i
        day["output"] += output_i


def _update_entry(path, project, session_id, size, mtime, inode):
    """Bring the cache entry for `path` up to date with what's on disk,
    parsing only newly appended complete lines. Returns the number of new
    bytes actually read (0 for a pure cache hit). Must be called with
    _lock held. Raises OSError if the file can't be opened/read, in which
    case nothing is committed to _cache: a file that has never been read
    successfully stays absent (not a bogus zero-valued entry), and a file
    that WAS cached keeps its last-known-good totals instead of being
    wiped by a transient failure. The caller counts a raise as skipped."""
    existing = _cache.get(path)
    if existing is not None and (existing["inode"] != inode or size < existing["size"]):
        # Rotated or truncated out from under us: the old offset and
        # dedup set no longer describe this file's contents. Don't reuse
        # the object in place, build fresh below, and only replace the
        # cached one once a read actually succeeds.
        existing = None
    entry = existing if existing is not None else _new_entry(project, session_id, inode)
    entry["inode"] = inode
    entry["project"] = project

    if size == entry["size"] and mtime == entry["mtime"]:
        _cache[path] = entry  # no-op for an already-cached, unchanged entry
        return 0  # unchanged since last read: reuse cached totals as-is

    start_offset = entry["offset"]
    data = _read_new_bytes(path, start_offset)  # may raise OSError; nothing committed if so

    pos = start_offset
    for line in data.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            # Partial trailing line (mid-write): don't consume it, leave
            # offset before it so the next poll re-reads it whole.
            break
        pos += len(line)
        _consume_line(entry, line)

    entry["offset"] = pos
    entry["size"] = size
    entry["mtime"] = mtime
    _cache[path] = entry
    _cache.move_to_end(path)
    return pos - start_offset


def _evict_missing():
    """Drop cache entries for files that no longer exist on disk. Called
    with _lock held; bounded by MAX_CACHE_ENTRIES so this is at most 2000
    stat-ish lookups."""
    for path in list(_cache.keys()):
        if not os.path.exists(path):
            del _cache[path]


def _enforce_cache_cap():
    """Evict least-recently-stat'ed entries (front of the OrderedDict)
    until the cache is back at or under MAX_CACHE_ENTRIES."""
    while len(_cache) > MAX_CACHE_ENTRIES:
        _cache.popitem(last=False)


def rollup(root=None, max_bytes_per_call=DEFAULT_MAX_BYTES_PER_CALL,
           now_fn=time.time, days=DEFAULT_DAYS):
    """Per-session and per-day token rollups for every transcript under
    `root` (default ~/.claude/projects, honouring CLAUDE_CONFIG_DIR).

    Reads incrementally via the module cache: unchanged files are served
    from cache without being opened, changed files have only their new
    bytes parsed. Newest-mtime-first, so live sessions stay current even
    when `max_bytes_per_call` (default 64 MiB, summed across every file
    touched this call) forces the rest to fall back to their last-known
    cached totals, in which case the result's "partial" is True.
    """
    root = _default_root() if root is None else root
    file_entries, discover_skipped = _discover_files(root)

    with _lock:
        _evict_missing()
        for path, _project, _size, _mtime, _inode in file_entries:
            if path in _cache:
                _cache.move_to_end(path)

        total_bytes_read = 0
        skipped = discover_skipped
        partial = False

        for path, project, size, mtime, inode in file_entries:
            if total_bytes_read >= max_bytes_per_call:
                partial = True
                continue
            session_id = os.path.splitext(os.path.basename(path))[0]
            try:
                bytes_read = _update_entry(path, project, session_id, size, mtime, inode)
            except OSError:
                skipped += 1
                continue
            total_bytes_read += bytes_read

        _enforce_cache_cap()

        now = now_fn()
        cutoff_str = (
            datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
            - datetime.timedelta(days=days)
        ).date().isoformat()

        sessions_out = {}
        daily_accum = {}
        for path, _project, _size, _mtime, _inode in file_entries:
            entry = _cache.get(path)
            if entry is None:
                continue  # never successfully read (e.g. always failed to open)
            totals = entry["totals"]
            sessions_out[entry["session_id"]] = {
                "session_id": entry["session_id"],
                "project": entry["project"],
                "input": totals["input"],
                "cache_read": totals["cache_read"],
                "cache_write": totals["cache_write"],
                "output": totals["output"],
                "effective": effective(**totals),
                "first_ts": entry["first_ts"],
                "last_ts": entry["last_ts"],
                "models": dict(entry["models"]),
                "messages": entry["messages"],
            }
            for date_str, day in entry["daily"].items():
                if date_str < cutoff_str:
                    continue
                bucket = daily_accum.setdefault(
                    date_str, {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0})
                bucket["input"] += day["input"]
                bucket["cache_read"] += day["cache_read"]
                bucket["cache_write"] += day["cache_write"]
                bucket["output"] += day["output"]

        daily_out = {
            date_str: dict(bucket, effective=effective(**bucket))
            for date_str, bucket in daily_accum.items()
        }

        return {
            "sessions": sessions_out,
            "daily": daily_out,
            "generated_at": now,
            "files": len(file_entries) + discover_skipped,
            "bytes_read": total_bytes_read,
            "skipped": skipped,
            "partial": partial,
        }


def session_usage(session_id, root=None):
    """One session's totals, or None if it hasn't been seen. Uses the
    same cache as rollup() (implemented as a thin lookup over a rollup()
    call, so a single code path owns the read/cache/dedup contract)."""
    data = rollup(root=root)
    return data["sessions"].get(session_id)


def reset_cache():
    """Test hook; also lets a caller force a full re-read of every file
    on the next rollup()/session_usage() call."""
    with _lock:
        _cache.clear()
