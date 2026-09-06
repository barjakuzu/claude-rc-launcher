"""Feature-detects the installed `claude` binary once at startup.

Older Claude Code builds lack --session-id, -n/--name, --remote-control,
--permission-mode, and `claude agents --json`. Every module that wants one
of those behaviors checks CAPS first and falls back to the pre-v3
keystroke-based path when the flag isn't there - this is the single place
that knows how to tell the difference, so an upgrade or downgrade of the
`claude` binary can't leave two modules disagreeing about what's supported.
"""
import json
import re
import subprocess

VERSION_RE = re.compile(r'(\d+\.\d+\.\d+)')


def _run_capture(run, cmd, timeout=10):
    """Run one probe command, returning stdout text or None on any failure
    (missing binary, non-zero exit for --help/--version, timeout)."""
    try:
        r = run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 and "agents" not in cmd:
        # --help/--version failing outright means this isn't a usable binary.
        return None
    return r.stdout


def detect_caps(claude_bin, run=subprocess.run):
    """Probe one `claude` binary for the v3 session-identity flags.

    Parses `claude --help` for flag names (cheap, no session created) and
    separately probes `claude agents --json` (also cheap: it lists live
    sessions, it does not start one). Any failure to run the binary at all
    (missing, not executable, times out) yields every flag False and
    version None rather than raising - callers must be able to trust CAPS
    even when claude isn't installed yet.
    """
    caps = {
        "session_id_flag": False,
        "name_flag": False,
        "remote_control_flag": False,
        "permission_mode_flag": False,
        "agents_json": False,
        "version": None,
    }
    help_text = _run_capture(run, [claude_bin, "--help"])
    if help_text is not None:
        caps["session_id_flag"] = "--session-id" in help_text
        caps["name_flag"] = bool(re.search(r'(^|\s)(-n,\s*)?--name(\s|,|<)', help_text, re.M))
        caps["remote_control_flag"] = "--remote-control" in help_text
        caps["permission_mode_flag"] = "--permission-mode" in help_text

    agents_out = _run_capture(run, [claude_bin, "agents", "--json"])
    if agents_out is not None:
        try:
            parsed = json.loads(agents_out)
            caps["agents_json"] = isinstance(parsed, list)
        except (ValueError, TypeError):
            caps["agents_json"] = False

    version_out = _run_capture(run, [claude_bin, "--version"])
    if version_out is not None:
        m = VERSION_RE.search(version_out)
        if m:
            caps["version"] = m.group(1)

    return caps


def refresh_caps(claude_bin=None):
    """Re-run detection and update the module-level CAPS in place. Tests
    use this to swap in a fake binary; production code calls it once,
    implicitly, at import time below."""
    global CAPS
    from config import CLAUDE_BIN
    CAPS = detect_caps(claude_bin or CLAUDE_BIN)
    return CAPS


def claude_version():
    return CAPS.get("version")


from config import CLAUDE_BIN as _CLAUDE_BIN  # noqa: E402  (after refresh_caps def, before use)
CAPS = detect_caps(_CLAUDE_BIN)
