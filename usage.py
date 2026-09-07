"""Turns Claude Code transcript JSONL files (~/.claude/projects/<encoded
cwd>/<session-uuid>.jsonl, plus each session's
<session-uuid>/subagents/agent-<id>.jsonl files) into per-session and
per-day token rollups.

Incremental: a module-level cache keyed by file path remembers how far
each file has been read so a poll every 30s only parses newly appended
bytes, not the whole history. See rollup() for the read/cache contract.

A session's own file and its subagents' files are separate files on disk
but the same session for accounting purposes: subagent usage rolls up
into the parent session's totals, keyed by the record's own "sessionId"
field rather than by filename, since a subagent file's name is
"agent-<id>.jsonl", not the session's uuid.

Security note (public repo): this module must never return a decoded cwd,
username, home path, or prompt/response content. "project" is only ever
the already-encoded ~/.claude/projects/<...> directory name.
"""
from __future__ import annotations

import collections
import datetime
import io
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

# A read counts as a "stall" when it was clipped by the byte budget and
# still yielded zero complete lines (a single line longer than what this
# attempt was allowed to read). After this many CONSECUTIVE stalls for
# one file, _update_entry stops spending real I/O on it every call and
# reports it as skipped/partial instead, so one pathological file can
# never silently monopolize the whole budget forever. See _update_entry.
MAX_CONSECUTIVE_STALLS = 3

# Cap on the number of tracked files so a box with years of transcripts
# can't grow the process without bound. Least-recently-stat'ed entries are
# evicted first once this is exceeded.
MAX_CACHE_ENTRIES = 2000

# Module-level cache, keyed by absolute transcript file path. An
# OrderedDict so "least recently stat'ed" eviction is a cheap
# move_to_end() on every visit plus a popitem(last=False) when over cap.
# Guarded by _lock for every _cache read and write. The lock is held only
# around cache bookkeeping, never around the disk read of a transcript
# file: _update_entry() acquires and releases it itself, per file, so a
# multi-file rollup() never blocks a concurrent session_usage() (or
# another rollup()) caller for the whole scan, only for whichever single
# file either side happens to be touching at that instant.
_cache = collections.OrderedDict()
_lock = threading.Lock()

# Round-robins which currently-stalled file (stall_count > 0) gets
# processed FIRST in a rollup() call, ahead of the normal newest-mtime-
# first order. Without this, a stalled file sits wherever its mtime
# happens to sort it: if even one newer file has anything new to read
# that call, this file's own remaining share of the budget drops below
# a full max_bytes_per_call and it's deferred (see _update_entry) again,
# every single call, forever, as long as that newer file stays active.
# Giving one stalled file the front of the queue each call guarantees it
# actually reaches a call where the full allowance is available. This IS
# load-bearing, not just a nicety: an int cursor that advances is what
# makes every currently-stalled file eventually get its turn (a liveness
# guarantee) rather than the same one file being retried while any
# others sharing the front of the queue starve behind it forever. See
# rollup() and PickStallPriorityPathRotatesTest.
_stall_priority_cursor = 0


def _as_int(value):
    """Coerce a raw usage field to int. Missing/non-int/negative values
    (including bool, which is technically an int subclass but never a
    legitimate token count) count as 0: a token count can't be negative,
    and a corrupt line must never raise."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value >= 0:
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
    the FIRST path segment under root, e.g. for
    root/<encoded-cwd>/<uuid>/subagents/agent-1.jsonl that's
    <encoded-cwd>, matching the top-level file
    root/<encoded-cwd>/<uuid>.jsonl's own project, not "subagents" or a
    "wf_..." workflow directory name (the already-encoded, already-lossy
    cwd Claude Code uses, never decoded here).

    Files that fail os.stat (raced away, permission denied on the
    directory entry) count toward `skipped` rather than raising. A root
    that can't be listed at all (missing, or not a directory) also counts
    toward `skipped` via the os.walk onerror hook, instead of silently
    reporting zero files (which would be indistinguishable from "no usage
    yet")."""
    entries = []
    skipped = 0

    def _count_walk_error(_exc):
        nonlocal skipped
        skipped += 1

    for dirpath, _dirnames, filenames in os.walk(root, onerror=_count_walk_error):
        project = os.path.relpath(dirpath, root).split(os.sep)[0]
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


def _new_entry(project, inode):
    return {
        "inode": inode,
        "size": 0,
        "mtime": 0.0,
        "offset": 0,
        "seen_keys": set(),
        "totals": {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0},
        "first_ts": None,
        "last_ts": None,
        # Filled in from the first usage-bearing record's own "sessionId"
        # field (see _consume_line); a file whose records never carry one
        # falls back to its filename stem, decided at aggregation time in
        # rollup(), not here, since that fallback needs the path.
        "session_id": None,
        "project": project,
        "models": {},
        "messages": 0,
        # Per-file daily buckets, date string -> raw totals dict. Kept
        # here (not recomputed from scratch) because the file may have
        # already scrolled out of the cache's read window by the time a
        # later rollup() call re-sums across files.
        "daily": {},
        # True once at least one read of this file has completed without
        # raising. Gates whether the entry is surfaced in rollup()'s
        # output: a file that has only ever failed to open (permissions,
        # raced away) must stay absent, not appear as a bogus all-zero
        # session.
        "synced": False,
        # Consecutive reads that were clipped by the budget and yielded
        # zero complete lines (see _update_entry). Reset to 0 by any read
        # that makes real progress. Once it reaches MAX_CONSECUTIVE_STALLS,
        # this file stops being read under any call whose max_bytes_per_call
        # is no bigger than "stall_bytes" below (round 4: giving up is
        # budget-relative, not permanent -- see _update_entry).
        "stall_count": 0,
        # The allowance (equal to max_bytes_per_call, at the call where
        # it failed) that most recently failed to make progress. A later
        # call offering more than this retries even past
        # MAX_CONSECUTIVE_STALLS.
        "stall_bytes": 0,
    }


def _read_new_bytes(path, offset, max_bytes):
    """Read up to `max_bytes` of a transcript file starting at `offset`.
    Its own function (rather than inlined) purely so tests can make one
    specific path fail to open/read (simulating a permission error or a
    file that vanished mid-read) without depending on real filesystem
    permission bits, which root ignores."""
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(max_bytes)


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

    # First usage-bearing record wins: subagent files carry the PARENT
    # session's id in their own "sessionId" field, which is how their
    # spend rolls up into the parent rather than becoming a phantom
    # session named after the subagent's filename.
    if entry["session_id"] is None:
        row_session_id = row.get("sessionId")
        if isinstance(row_session_id, str) and row_session_id:
            entry["session_id"] = row_session_id

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


def _merge_scratch(entry, scratch):
    """Fold a scratch entry's freshly-parsed contribution into the live
    shared `entry`, additively (never overwrites totals/messages/models/
    daily, only adds to them: `entry` may already carry contributions
    from earlier reads of this same file)."""
    entry["seen_keys"] = scratch["seen_keys"]
    entry["session_id"] = scratch["session_id"]
    for k in ("input", "cache_read", "cache_write", "output"):
        entry["totals"][k] += scratch["totals"][k]
    entry["messages"] += scratch["messages"]
    for model, count in scratch["models"].items():
        entry["models"][model] = entry["models"].get(model, 0) + count
    if scratch["first_ts"] is not None and (
            entry["first_ts"] is None or scratch["first_ts"] < entry["first_ts"]):
        entry["first_ts"] = scratch["first_ts"]
    if scratch["last_ts"] is not None and (
            entry["last_ts"] is None or scratch["last_ts"] > entry["last_ts"]):
        entry["last_ts"] = scratch["last_ts"]
    for date_str, day in scratch["daily"].items():
        bucket = entry["daily"].setdefault(
            date_str, {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0})
        bucket["input"] += day["input"]
        bucket["cache_read"] += day["cache_read"]
        bucket["cache_write"] += day["cache_write"]
        bucket["output"] += day["output"]


def _update_entry(path, project, max_bytes, max_bytes_per_call):
    """Bring the cache entry for `path` up to date with what's on disk,
    parsing only newly appended complete lines, clipped to at most
    `max_bytes` of new data (the caller's remaining per-call budget),
    except a file that stalled on a previous attempt is only attempted
    at all when a full `max_bytes_per_call` is still available this call
    (see the "no-progress reads" paragraph below); nothing ever grants
    it more than `max_bytes`. Returns (bytes_read, clipped, gave_up). Raises
    OSError if the file can't be stat'ed/opened/read; nothing is
    committed in that case, so a file that has never been read
    successfully stays absent from rollup()'s output (see the "synced"
    field) rather than appearing as a bogus zero-valued session, and a
    file that WAS already synced keeps its last-known-good totals
    instead of being wiped by a transient failure.

    No-progress reads: a single JSONL line longer than what a given
    attempt is allowed to read can never resolve into a complete line,
    so `bytes_read` is nonzero (Important 2, round 2) but `offset` never
    advances. Left alone, this can consume this file's entire allotted
    budget on every single call forever, and if that allotment happens
    to be most or all of `max_bytes_per_call`, every OTHER file in the
    same call is starved behind it too. Mitigations, tracked via the
    entry's "stall_count" and "stall_bytes":
    - A file with a prior stall (stall_count > 0) is only ever attempted
      when this call's remaining budget (`max_bytes`) equals a full
      `max_bytes_per_call` (see the defer branch below): enough, once
      attempted, to finish an oversized-but-not-pathological line in one
      shot rather than being clipped again at the exact same place by
      whatever smaller fraction of the shared budget happened to be left
      this time. If that much isn't available this call (something else
      went first), the attempt is deferred to a call where it is,
      rather than spending I/O on a read that's doomed to stall again.
    - Once stall_count reaches MAX_CONSECUTIVE_STALLS, no further read is
      attempted UNDER A CALL WHOSE max_bytes_per_call IS NO BIGGER THAN
      "stall_bytes" (the allowance that most recently failed): the
      caller is told to count this as skipped and mark the result
      partial. Giving up is deliberately budget-relative, not permanent:
      round 3 tried "give up until a rotation/truncation resets the
      entry", but a rotation is not guaranteed (delete-and-recreate can
      reuse the same inode) and abandonment at offset 0 (the common case,
      since the oversized line is usually the file's first) can never
      satisfy the reset condition (`size < offset` is unsatisfiable at
      offset 0) -- so that design could freeze a live session's number
      forever with no way out. A later call whose max_bytes_per_call
      genuinely exceeds what already failed retries instead, however
      many times stall_count has already climbed past the limit.
    A read that DOES make progress (at least one complete line) resets
    both stall_count and stall_bytes immediately, however small that
    progress was.

    Does its OWN os.stat, inside the first locked block, rather than
    trusting the caller's pre-scan (size, mtime, inode): the pre-scan
    runs once per rollup() call, before the per-file loop, so by the
    time a given file's turn comes up (after every earlier file in the
    call has been processed) that snapshot can be badly stale, and a
    concurrent caller may already have advanced this same entry past it.
    A stale, too-small pre-scan size compared against a newer offset
    reads as a truncation that never happened, resetting a live session's
    entry (and briefly making it vanish from rollup()'s output, since a
    freshly reset entry is unsynced until its own read completes). A
    fresh stat taken atomically with the cache lookup, in the same locked
    block, closes that window: it can only be as stale as the read that
    follows it, the same residual (self-healing within one more poll)
    slack every other field in this cache already tolerates.

    Acquires the module lock itself, twice: once (briefly) to stat the
    file, look up or create the cache entry, and decide whether a read is
    even needed, snapshotting its dedup set and offset; again (also
    briefly) to commit. The disk read, and the JSON/line parsing, run
    with NO lock held and touch no shared state: they parse into a
    private scratch entry (see _merge_scratch), never the live one, so
    two callers racing on the same file (a poll and a concurrent
    session_usage() call, say) never mutate the same dict/set at the same
    time. This is what keeps a multi-file rollup() from blocking a
    concurrent caller for the whole scan: the lock is only ever held for
    one file's cheap bookkeeping, not its I/O.

    If another caller has already advanced (or reset) this same entry by
    the time we finish reading (detected by the live entry's offset no
    longer matching what we snapshotted), our scratch result is discarded
    rather than merged: whichever caller commits first wins, and the
    loser's data will simply be re-read on the next rollup() call.
    """
    with _lock:
        st = os.stat(path)  # may raise OSError; caller counts it as skipped
        size, mtime, inode = st.st_size, st.st_mtime, st.st_ino

        existing = _cache.get(path)
        if existing is not None and (existing["inode"] != inode or size < existing["offset"]):
            # Rotated or truncated out from under us: the old offset and
            # dedup set no longer describe this file's contents.
            existing = None
        if existing is None:
            entry = _new_entry(project, inode)
            _cache[path] = entry
        else:
            entry = existing
            entry["inode"] = inode
            entry["project"] = project

        if size == entry["size"] and mtime == entry["mtime"]:
            _cache.move_to_end(path)
            return 0, False, False  # unchanged since last read: nothing to do

        if (entry["stall_count"] >= MAX_CONSECUTIVE_STALLS
                and max_bytes_per_call <= entry["stall_bytes"]):
            # Given up on this file UNDER THIS BUDGET: MAX_CONSECUTIVE_STALLS
            # consecutive reads, at an allowance at least this big, still
            # resolved zero complete lines. This is relative to
            # max_bytes_per_call, not permanent: a later call offering a
            # bigger overall budget than the one that last failed
            # (max_bytes_per_call > entry["stall_bytes"]) is NOT caught by
            # this check and retries below, no matter how high stall_count
            # has climbed. (There is no rotation/truncation escape hatch to
            # rely on instead: a rotation is not guaranteed to happen, and
            # abandonment at offset 0 -- the common case -- could never
            # satisfy the size-based reset check above even if one did.)
            _cache.move_to_end(path)
            return 0, False, True

        if entry["stall_count"] > 0 and max_bytes < max_bytes_per_call:
            # This file needs a full max_bytes_per_call to have earned
            # another try (see the docstring), but something else in this
            # call already spent part of the shared budget, so max_bytes
            # (this file's remaining share) is less than that. Don't
            # attempt a smaller read that's doomed to stall again for no
            # reason but bad luck in ordering -- and don't grant the full
            # allowance anyway, blowing past max_bytes_per_call, the way
            # an earlier version of this retry logic did (round 4,
            # Minor: a call once read 1.98x its documented cap this way).
            # Defer to a call where the full allowance is
            # actually available; this is not "given up" (no I/O spent,
            # stall_count/stall_bytes untouched), just deferred, so it
            # still marks the result partial without counting as skipped.
            _cache.move_to_end(path)
            return 0, True, False

        start_offset = entry["offset"]
        # By construction here, either stall_count == 0 (max_bytes is
        # this file's ordinary fair share) or stall_count > 0 and
        # max_bytes == max_bytes_per_call exactly (the defer branch above
        # already ruled out max_bytes < max_bytes_per_call, and max_bytes
        # can never exceed it). Either way max_bytes IS the allowance to
        # use directly; no separate, larger value is ever computed.
        effective_max = max_bytes
        # Cap the actual read at what the file really has pending, not
        # the full allowance: f.read(n) can allocate close to n bytes up
        # front even when far less is available, and with a generous
        # max_bytes_per_call that can mean allocating tens of MB to read
        # a 200-byte appendix. `clipped` below still compares against
        # `effective_max`, not this narrower read size, so a file that
        # genuinely has more pending than its allowance is still
        # correctly flagged.
        pending = max(size - start_offset, 0)
        read_cap = min(effective_max, pending)
        seen_snapshot = set(entry["seen_keys"])
        session_id_snapshot = entry["session_id"]

    # --- disk I/O and parsing, no lock held, no shared state touched ---
    data = _read_new_bytes(path, start_offset, read_cap)  # may raise OSError
    clipped = len(data) >= effective_max

    scratch = _new_entry(project, inode)
    scratch["seen_keys"] = seen_snapshot
    scratch["session_id"] = session_id_snapshot

    # Iterate instead of data.splitlines(keepends=True): splitlines
    # materializes a list holding every line's own bytes copy on top of
    # `data` itself, which peaked at roughly 2x a full budget's worth of
    # data in practice. A BytesIO reader yields (and releases) one line
    # at a time. This also happens to be stricter than splitlines, which
    # additionally splits on bare \r: JSONL lines are only ever
    # terminated by \n, so this can't mis-split on a stray \r byte.
    pos = start_offset
    for line in io.BytesIO(data):
        if not line.endswith(b"\n"):
            # Partial trailing line (mid-write, or the read was clipped
            # mid-line): don't consume it, leave offset before it so the
            # next poll re-reads it whole (or with more budget).
            break
        pos += len(line)
        _consume_line(scratch, line)

    with _lock:
        current = _cache.get(path)
        if current is None or current is not entry or current["offset"] != start_offset:
            # Someone else already advanced (or reset) this entry while
            # we were reading unlocked. Merging our scratch on top would
            # double count or resurrect data a rotation already
            # discarded, so drop it; the next rollup() call will simply
            # re-read whatever this call didn't get to.
            return 0, False, False
        made_progress = pos > start_offset
        _merge_scratch(entry, scratch)
        entry["offset"] = pos
        # A clipped read stopped short of the file's true current EOF on
        # purpose (the budget), so only vouch for what was actually
        # parsed (pos): there's more, already-complete, data waiting.
        # An unclipped read reached the file's true EOF at read time,
        # which may differ from the pre-read `size` if the file grew (or,
        # rarely, shrank) between the stat and the read, so vouch for
        # what was actually observed (start_offset + len(data)) rather
        # than the possibly-stale stat value.
        entry["size"] = pos if clipped else start_offset + len(data)
        entry["mtime"] = mtime
        entry["synced"] = True
        if made_progress:
            entry["stall_count"] = 0
            entry["stall_bytes"] = 0
        elif clipped:
            # Clipped AND zero complete lines: a genuine no-progress
            # stall (this attempt's allowance, possibly already the full
            # max_bytes_per_call, wasn't enough), not the ordinary case
            # of a partial trailing line waiting on the writer (that's
            # unclipped: we read everything currently available and it
            # just isn't a complete line yet). Record the allowance that
            # failed, so a later call is only re-attempted once it can
            # offer more than this (see the give-up check above).
            entry["stall_count"] += 1
            entry["stall_bytes"] = effective_max
        _cache.move_to_end(path)
        # Charge the full number of bytes actually read against the
        # caller's budget, not just the span that resolved into complete
        # lines: a read that is clipped mid-line (a single line longer
        # than this call's remaining budget) still consumed real I/O and
        # must count for it, or the caller's budget accounting thinks
        # this file cost nothing while it silently re-reads the same
        # unfinished line, at real disk cost, on every future call too.
        # entry["offset"] not advancing past it is fine: the line is
        # still there, complete, waiting for a call with enough budget to
        # finish it (see ClippedLineTest / BudgetStallRecoveryTest).
        return len(data), clipped, False


def _evict_missing(discovered_paths):
    """Drop cache entries for files that no longer exist on disk. Only
    checks paths NOT in `discovered_paths` (files this call's os.walk
    already confirmed exist via a successful stat), so a steady-state
    poll against an unchanged root does zero exists() calls instead of
    up to MAX_CACHE_ENTRIES every time. Called with _lock held."""
    for path in list(_cache.keys()):
        if path in discovered_paths:
            continue
        if not os.path.exists(path):
            del _cache[path]


def _enforce_cache_cap():
    """Evict least-recently-stat'ed entries (front of the OrderedDict)
    until the cache is back at or under MAX_CACHE_ENTRIES."""
    while len(_cache) > MAX_CACHE_ENTRIES:
        _cache.popitem(last=False)


def _pick_stall_priority_path(file_entries):
    """The one currently-stalled file (stall_count > 0) among
    `file_entries` to process FIRST this rollup() call, or None if none
    of them are stalled. Rotates (a module-level cursor) across however
    many qualify right now, so repeated calls eventually give every
    stalled file a turn at the front of the queue, not just whichever
    one happens to have the newest mtime (which could be none of them,
    forever, while some other file keeps being newer)."""
    global _stall_priority_cursor
    with _lock:
        stalled = sorted(
            path for path, _project, _size, _mtime, _inode in file_entries
            if path in _cache and _cache[path]["stall_count"] > 0
        )
        if not stalled:
            return None
        idx = _stall_priority_cursor % len(stalled)
        _stall_priority_cursor += 1
        return stalled[idx]


def rollup(root=None, max_bytes_per_call=DEFAULT_MAX_BYTES_PER_CALL,
           now_fn=time.time, days=DEFAULT_DAYS):
    """Per-session and per-day token rollups for every transcript under
    `root` (default ~/.claude/projects, honouring CLAUDE_CONFIG_DIR).

    A session's own file and its subagents/agent-*.jsonl files are summed
    together under the session id carried in their records (see
    _consume_line), not overwritten: multiple files legitimately
    contribute to one session's totals, and each file keeps its own
    (message.id, requestId) dedup set (see _update_entry / _consume_line)
    rather than sharing one across files.

    Reads incrementally via the module cache: unchanged files are served
    from cache without being opened, changed files have only their new
    bytes parsed, clipped to `max_bytes_per_call` (default 64 MiB, summed
    across every file touched this call). Newest-mtime-first, so live
    sessions stay current even when the budget forces the rest to fall
    back to their last-known cached totals or a single file's read to be
    clipped short of its true pending data, in which case the result's
    "partial" is True. The one exception to newest-mtime-first: one
    currently-stalled file, if any (see _update_entry), is processed
    FIRST regardless of its mtime, rotating across calls (see
    _pick_stall_priority_path). A stalled file is only ever attempted
    when it can be offered a full max_bytes_per_call; without going
    first, a stalled file whose mtime doesn't happen to be the newest
    would see its own remaining share shrink below that the moment ANY
    newer file has anything to read, deferring it again on every single
    call for as long as that newer file stays active, indefinitely.

    Tradeoff of going first: if the prioritized file stalls again, its
    attempt still consumes the whole max_bytes_per_call it was offered,
    so every OTHER file can see zero of this call's budget. Measured as
    bounded and self-terminating, not a new way to starve the rest of
    the tree: with K files simultaneously stalled, worst-case
    consecutive calls where some non-prioritized file gets starved scale
    at roughly 2K, one-time (it does not recur once every stalled file
    has either recovered or hit its own give-up threshold and stopped
    being attempted at all under that budget).
    """
    root = _default_root() if root is None else root
    file_entries, discover_skipped = _discover_files(root)
    discovered_paths = {e[0] for e in file_entries}

    with _lock:
        _evict_missing(discovered_paths)

    total_bytes_read = 0
    skipped = discover_skipped
    partial = False

    # Newest-mtime-first, same order as file_entries, so a byte budget
    # that runs out mid-call spends itself on the most active sessions
    # first: _update_entry() does its own (per-file) locking around this,
    # and its own fresh os.stat (the pre-scan's size/mtime/inode below are
    # used only for this ordering and for discovered_paths/LRU purposes,
    # never trusted for _update_entry's own truncation/change decisions).
    # One exception: a rotating, currently-stalled file (if any) is moved
    # to the very front, ahead of even the newest file, so it actually
    # gets a call where nothing has spent any of the shared budget yet
    # (see _pick_stall_priority_path and the docstring above).
    priority_path = _pick_stall_priority_path(file_entries)
    processing_order = file_entries
    if priority_path is not None:
        processing_order = (
            [e for e in file_entries if e[0] == priority_path]
            + [e for e in file_entries if e[0] != priority_path]
        )
    for path, project, _size, _mtime, _inode in processing_order:
        remaining = max_bytes_per_call - total_bytes_read
        if remaining <= 0:
            partial = True
            continue
        try:
            bytes_read, clipped, gave_up = _update_entry(path, project, remaining, max_bytes_per_call)
        except OSError:
            skipped += 1
            continue
        if gave_up:
            # MAX_CONSECUTIVE_STALLS consecutive no-progress reads: this
            # file is not being attempted this call at all, so it must
            # still be visible as an incomplete part of the snapshot,
            # exactly like any other file that failed to open.
            skipped += 1
            partial = True
            continue
        total_bytes_read += bytes_read
        if clipped:
            partial = True

    with _lock:
        # Bump every file this call stat'ed (whether or not it was
        # actually read: os.stat happened for all of them in
        # _discover_files) to reflect "least recently stat'ed" LRU order,
        # authoritatively overriding whatever order _update_entry's own
        # per-file move_to_end() calls left things in above (which runs
        # newest-first for budget prioritization, not LRU purposes).
        # Bumping in oldest-mtime-first order here means the LAST
        # move_to_end() call (the newest file) ends up truly last, i.e.
        # most protected from _enforce_cache_cap()'s popitem(last=False),
        # which evicts from the front. Bumping newest-first would do the
        # opposite: push the most active files toward the front, where
        # they'd be the FIRST evicted past the cache cap.
        for path, _project, _size, _mtime, _inode in reversed(file_entries):
            if path in _cache:
                _cache.move_to_end(path)

        _enforce_cache_cap()

        now = now_fn()
        cutoff_str = (
            datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
            - datetime.timedelta(days=days)
        ).date().isoformat()

        # Merge per-file cache entries into per-session accumulators.
        # Multiple files (a session's own transcript plus any of its
        # subagents/agent-*.jsonl files) can map to the same session id
        # and must SUM, not overwrite: each is a genuinely different
        # slice of that session's cost.
        session_accum = {}
        for path, _project, _size, _mtime, _inode in file_entries:
            entry = _cache.get(path)
            if entry is None or not entry["synced"]:
                continue  # never successfully read (e.g. always failed to open)
            if entry["session_id"] is None and entry["messages"] == 0:
                # No usage-bearing record ever gave this file a session
                # identity, and it never counted any usage either: falling
                # back to its filename stem would merge every other
                # usage-free file with the same name (several different
                # journal.jsonl files, say) into one meaningless phantom
                # session. Nothing here is worth reporting.
                continue
            session_id = entry["session_id"] or os.path.splitext(os.path.basename(path))[0]
            acc = session_accum.get(session_id)
            if acc is None:
                acc = {
                    "session_id": session_id,
                    "project": entry["project"],
                    "input": 0, "cache_read": 0, "cache_write": 0, "output": 0,
                    "first_ts": None, "last_ts": None,
                    "models": {}, "messages": 0,
                    "daily": {},
                }
                session_accum[session_id] = acc
            totals = entry["totals"]
            acc["input"] += totals["input"]
            acc["cache_read"] += totals["cache_read"]
            acc["cache_write"] += totals["cache_write"]
            acc["output"] += totals["output"]
            acc["messages"] += entry["messages"]
            for model, count in entry["models"].items():
                acc["models"][model] = acc["models"].get(model, 0) + count
            if entry["first_ts"] is not None and (
                    acc["first_ts"] is None or entry["first_ts"] < acc["first_ts"]):
                acc["first_ts"] = entry["first_ts"]
            if entry["last_ts"] is not None and (
                    acc["last_ts"] is None or entry["last_ts"] > acc["last_ts"]):
                acc["last_ts"] = entry["last_ts"]
            for date_str, day in entry["daily"].items():
                bucket = acc["daily"].setdefault(
                    date_str, {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0})
                bucket["input"] += day["input"]
                bucket["cache_read"] += day["cache_read"]
                bucket["cache_write"] += day["cache_write"]
                bucket["output"] += day["output"]

        sessions_out = {}
        daily_accum = {}
        for session_id, acc in session_accum.items():
            sessions_out[session_id] = {
                "session_id": session_id,
                "project": acc["project"],
                "input": acc["input"],
                "cache_read": acc["cache_read"],
                "cache_write": acc["cache_write"],
                "output": acc["output"],
                "effective": effective(
                    input=acc["input"], cache_read=acc["cache_read"],
                    cache_write=acc["cache_write"], output=acc["output"]),
                "first_ts": acc["first_ts"],
                "last_ts": acc["last_ts"],
                "models": acc["models"],
                "messages": acc["messages"],
            }
            for date_str, day in acc["daily"].items():
                if date_str <= cutoff_str:  # strict: `days` buckets, not days+1
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
    call, so a single code path owns the read/cache/dedup/merge
    contract)."""
    data = rollup(root=root)
    return data["sessions"].get(session_id)


def reset_cache():
    """Test hook; also lets a caller force a full re-read of every file
    on the next rollup()/session_usage() call."""
    global _stall_priority_cursor
    with _lock:
        _cache.clear()
        _stall_priority_cursor = 0
