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
    """Sorted (oldest first) list of "<date>.jsonl" filenames present."""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    out = []
    for n in names:
        if n.endswith(".jsonl"):
            try:
                datetime.datetime.strptime(n[:-len(".jsonl")], DATE_FMT)
            except ValueError:
                continue
            out.append(n)
    return sorted(out)


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
    if cursor_file in files:
        start_index = files.index(cursor_file)
    else:
        # Unknown/older/missing file (pruned, or first-ever read): start
        # from the beginning of the earliest file still present.
        start_index = 0
        cursor_offset = 0

    rows = []
    last_file, last_offset = files[start_index], cursor_offset
    for i in range(start_index, len(files)):
        fname = files[i]
        path = os.path.join(root, fname)
        offset = cursor_offset if fname == cursor_file else 0
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
        for line in data.splitlines(keepends=True):
            line_len = len(line)
            if not line.endswith(b"\n"):
                # Truncated last line (mid-write): stop here, do not
                # advance the cursor past it, so it gets re-read whole
                # next time once the writer finishes.
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
