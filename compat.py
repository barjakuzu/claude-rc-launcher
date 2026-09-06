"""Feature-detects the installed `claude` binary, lazily, once.

Older Claude Code builds lack --session-id, -n/--name, --remote-control,
--permission-mode, and `claude agents --json`. Every module that wants one
of those behaviors calls get_caps() first and falls back to the pre-v3
keystroke-based path when the flag isn't there - this is the single place
that knows how to tell the difference, so an upgrade or downgrade of the
`claude` binary can't leave two modules disagreeing about what's supported.

Detection is lazy and memoized: importing this module never shells out
(so `import server`, and every test that imports it, stays fast). The
first call to get_caps() runs the probe and caches the result in CAPS;
later calls reuse it. CAPS is a module-level dict that is mutated in
place (never rebound) so `from compat import CAPS` stays valid across a
refresh_caps() call.
"""
import config
import json
import re
import subprocess

VERSION_RE = re.compile(r'(\d+\.\d+\.\d+)')

SESSION_ID_RE = re.compile(r'(^|\s)--session-id(\s|,|<|=|$)', re.M)
NAME_RE = re.compile(r'(^|\s)--name(\s|,|<|=|$)', re.M)
REMOTE_CONTROL_RE = re.compile(r'(^|\s)--remote-control(\s|,|<|=|$)', re.M)
PERMISSION_MODE_RE = re.compile(r'(^|\s)--permission-mode(\s|,|<|=|$)', re.M)

# CAPS starts empty; get_caps() populates it on first call. Never rebind
# this name - always CAPS.clear()/CAPS.update() so existing references
# (`from compat import CAPS`) keep seeing updates.
CAPS = {}
_detected = False


def _run_capture(run, cmd, timeout=5, allow_nonzero=False):
    """Run one probe command, returning stdout text or None on any failure
    (missing binary, non-zero exit when not allowed, timeout)."""
    try:
        r = run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 and not allow_nonzero:
        # --help/--version failing outright means this isn't a usable binary.
        return None
    return r.stdout


def detect_caps(claude_bin, run=subprocess.run):
    """Probe one `claude` binary for the v3 session-identity flags.

    Parses `claude --help` for flag names (cheap, no session created) and
    separately probes `claude agents --json` (also cheap: it lists live
    sessions, it does not start one). Any failure to run the binary at all
    (missing, not executable, times out) yields every flag False and
    version None rather than raising - callers must be able to trust the
    result even when claude isn't installed yet.
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
        caps["session_id_flag"] = bool(SESSION_ID_RE.search(help_text))
        caps["name_flag"] = bool(NAME_RE.search(help_text))
        caps["remote_control_flag"] = bool(REMOTE_CONTROL_RE.search(help_text))
        caps["permission_mode_flag"] = bool(PERMISSION_MODE_RE.search(help_text))

    agents_out = _run_capture(run, [claude_bin, "agents", "--json"], allow_nonzero=True)
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


def get_caps():
    """Return the cached capability dict, detecting once on first call.

    Reads `config.CLAUDE_BIN` (which itself honours the `RC_CLAUDE_BIN`
    env var), so pointing that env var at a fake binary before the first
    call - as tests do - changes what gets detected.
    """
    global _detected
    if not _detected:
        CAPS.clear()
        CAPS.update(detect_caps(config.CLAUDE_BIN))
        _detected = True
    return CAPS


def refresh_caps(claude_bin=None):
    """Force a fresh detection pass and update CAPS in place. Tests use
    this to swap in a fake binary; production code never needs to call
    this after the first get_caps()."""
    global _detected
    CAPS.clear()
    CAPS.update(detect_caps(claude_bin or config.CLAUDE_BIN))
    _detected = True
    return CAPS


def claude_version():
    return get_caps().get("version")
