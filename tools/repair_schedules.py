#!/usr/bin/env python3
"""Repair a corrupted schedules.json by recovering the longest valid JSON
prefix. The observed failure mode is trailing garbage after a valid array
(e.g. a stray ']' appended past the closing ']'), which makes json.load
raise "Extra data" on the whole file even though the real content parses
fine up to that point.

Usage: python3 tools/repair_schedules.py <path-to-schedules.json>

Backs up the original file to <path>.corrupt-<timestamp>, then atomically
rewrites <path> with the recovered, re-serialized JSON (mode 0600). Leaves
the file untouched if it already parses cleanly, or if no valid JSON list
can be recovered at all.

Example: python3 tools/repair_schedules.py ~/.claude-rc/schedules.json
"""
import json
import os
import shutil
import sys
import tempfile
import time


def recover_longest_prefix(text):
    """Return (value, trailing_text) for the longest valid JSON value
    parseable from the start of `text`, or (None, None) if nothing parses."""
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(text)
    except json.JSONDecodeError:
        return None, None
    return value, text[end:]


def repair(path):
    with open(path, "r") as f:
        original = f.read()

    try:
        json.loads(original)
        print(f"{path} already parses cleanly; nothing to do.")
        return True
    except json.JSONDecodeError as e:
        print(f"{path} failed to parse: {e}")

    value, trailing = recover_longest_prefix(original)
    if value is None:
        print("Could not recover any valid JSON prefix. Not touching the file.")
        return False
    if not isinstance(value, list):
        print(f"Recovered value is a {type(value).__name__}, not a list. Not touching the file.")
        return False

    backup_path = f"{path}.corrupt-{time.strftime('%Y%m%dT%H%M%S')}"
    shutil.copyfile(path, backup_path)
    print(f"Backed up original to {backup_path}")
    if trailing.strip():
        print(f"Discarded trailing data: {trailing.strip()[:200]!r}")

    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(prefix=".schedules-repair-", suffix=".json.tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, indent=2)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    print(f"Recovered {len(value)} schedule(s) and rewrote {path}.")
    return True


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 tools/repair_schedules.py <path-to-schedules.json>", file=sys.stderr)
        sys.exit(1)
    path = sys.argv[1]
    if not os.path.isfile(path):
        print(f"No such file: {path}", file=sys.stderr)
        sys.exit(1)
    ok = repair(path)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
