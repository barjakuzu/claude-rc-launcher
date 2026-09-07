"""Reads the JSONL event spool rc-hook (hooks/rc-hook) writes to
~/.claude-rc/events/<YYYY-MM-DD>.jsonl. Pure functions, root is always
passed in (never reads config.RC_HOME itself) so tests and fleet.py both
control it explicitly.
"""
import datetime
import json
import os
import time

DATE_FMT = "%Y-%m-%d"


def _spool_files(root):
    """Sorted (oldest first) list of spool filenames present: both
    "<date>.jsonl" and its rotated "<date>.jsonl.1" sibling (rc-hook
    renames the just-written file aside to ".1" once it exceeds
    ROTATE_SIZE_BYTES, then starts a fresh "<date>.jsonl"). For a given
    date the ".1" file holds strictly older events than the plain file
    (it's the pre-rotation content, a pure rename), so it is ordered
    immediately before it."""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    out = []
    for n in names:
        if n.endswith(".jsonl.1"):
            date_str = n[:-len(".jsonl.1")]
        elif n.endswith(".jsonl"):
            date_str = n[:-len(".jsonl")]
        else:
            continue
        try:
            datetime.datetime.strptime(date_str, DATE_FMT)
        except ValueError:
            continue
        out.append(n)
    # Sort by (date, is_rotated_first) so "<date>.jsonl.1" sorts right
    # before "<date>.jsonl" for the same date.
    return sorted(out, key=lambda n: (n[:-len(".jsonl.1")] if n.endswith(".jsonl.1") else n[:-len(".jsonl")],
                                       0 if n.endswith(".jsonl.1") else 1))


def _parse_cursor(cursor):
    if not cursor or ":" not in cursor:
        return None, 0
    filename, _, offset = cursor.rpartition(":")
    try:
        return filename, int(offset)
    except ValueError:
        return None, 0


def read_events(root, since_cursor=None, limit=500):
    """Rows newer than since_cursor (oldest first), capped at limit, and
    the cursor to resume from next time. Tolerant of day-boundary
    rotation and of a truncated last line in the current file."""
    files = _spool_files(root)
    if not files:
        return [], None

    cursor_file, cursor_offset = _parse_cursor(since_cursor)
    rotated_sibling = (cursor_file + ".1") if cursor_file else None
    plain_file_too_small = False
    if cursor_file in files:
        try:
            plain_file_too_small = cursor_offset > os.path.getsize(os.path.join(root, cursor_file))
        except OSError:
            plain_file_too_small = False
    if cursor_file in files and not (plain_file_too_small and rotated_sibling in files):
        start_index = files.index(cursor_file)
        start_file = cursor_file
    elif rotated_sibling and rotated_sibling in files:
        # The file the cursor pointed at has since been rotated aside:
        # rc-hook rotates by os.replace(path, path + ".1"), a pure
        # rename, so the bytes at the cursor's offset now live in the
        # ".1" sibling at the same offset. Resume there so the events
        # that were moved by rotation aren't skipped.
        start_index = files.index(cursor_file + ".1")
        start_file = cursor_file + ".1"
    else:
        # Unknown/older/missing file (pruned, or first-ever read): start
        # from the beginning of the earliest file still present.
        start_index = 0
        cursor_offset = 0
        start_file = files[0]

    rows = []
    last_file, last_offset = files[start_index], cursor_offset
    for i in range(start_index, len(files)):
        fname = files[i]
        path = os.path.join(root, fname)
        offset = cursor_offset if fname == start_file else 0
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if offset > size:
            offset = 0  # file was rotated/truncated externally; restart it
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
        pos = offset
        truncated = False
        for line in data.splitlines(keepends=True):
            line_len = len(line)
            if not line.endswith(b"\n"):
                # Truncated last line (mid-write): stop here, do not
                # advance the cursor past it, so it gets re-read whole
                # next time once the writer finishes. Also stop scanning
                # further (newer) files this call, so a day-boundary
                # rollover can't let the cursor skip past this line.
                truncated = True
                break
            pos += line_len
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
                if len(rows) >= limit:
                    last_file, last_offset = fname, pos
                    return rows, f"{last_file}:{last_offset}"
        last_file, last_offset = fname, pos
        if truncated:
            break

    return rows, f"{last_file}:{last_offset}"


def prune(root, days=7, now_fn=time.time):
    """Delete spool files older than `days`. Returns the list of deleted
    filenames (empty if root doesn't exist or nothing was old enough)."""
    try:
        files = _spool_files(root)
    except OSError:
        return []
    if not files:
        return []
    cutoff = datetime.datetime.fromtimestamp(now_fn(), datetime.timezone.utc).date() - datetime.timedelta(days=days)
    deleted = []
    for fname in files:
        date_str = fname[:-len(".jsonl")]
        try:
            file_date = datetime.datetime.strptime(date_str, DATE_FMT).date()
        except ValueError:
            continue
        if file_date < cutoff:
            try:
                os.remove(os.path.join(root, fname))
                deleted.append(fname)
            except OSError:
                pass
    return deleted
