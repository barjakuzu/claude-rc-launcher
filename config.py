"""Shared configuration for Claude RC Launcher."""

import os

VERSION = "2.1.9"

# Single home directory for all claude-rc data
RC_HOME = os.environ.get("RC_HOME", os.path.expanduser("~/.claude-rc"))

HOST = os.environ.get("RC_HOST", "0.0.0.0")
PORT = int(os.environ.get("RC_PORT", "8200"))
SESSION_PREFIX = os.environ.get("RC_PREFIX", "rc-")
WORKING_DIR = os.environ.get("RC_WORKING_DIR", ".")
CLAUDE_BIN = os.path.expanduser(os.environ.get("RC_CLAUDE_BIN", "claude"))
AUTH_USER = os.environ.get("RC_AUTH_USER", "")
AUTH_PASS = os.environ.get("RC_AUTH_PASS", "")
RC_ROLE = os.environ.get("RC_ROLE", "full")
if RC_ROLE not in ("full", "metadata"):
    print(f"Warning: invalid RC_ROLE={RC_ROLE!r}, defaulting to 'full'")
    RC_ROLE = "full"
SHELL_BIN = os.environ.get("RC_SHELL_BIN") or os.environ.get("SHELL") or "/bin/bash"
RC_TRUSTED_PROXIES = set(
    p.strip() for p in os.environ.get("RC_TRUSTED_PROXIES", "127.0.0.1,::1").split(",")
    if p.strip()
)
RC_BEHIND_TLS = os.environ.get("RC_BEHIND_TLS", "") == "1"
try:
    RC_MAX_SESSIONS = int(os.environ.get("RC_MAX_SESSIONS", "10"))
except ValueError:
    print(f"Warning: invalid RC_MAX_SESSIONS={os.environ.get('RC_MAX_SESSIONS')!r}, disabling the session cap")
    RC_MAX_SESSIONS = 0

# Resolve relative working dir to absolute
WORKING_DIR = os.path.abspath(WORKING_DIR)

# Mode for plain terminal sessions: tmux runs SHELL_BIN instead of Claude Code.
# It carries no Claude flags, so its RC_FLAGS entry is a sentinel that only keeps
# the mode valid for /start's `mode not in RC_FLAGS` check.
SHELL_MODE = "sh"

RC_FLAGS = {
    "c": "--dangerously-skip-permissions --verbose",
    "ci": "--dangerously-skip-permissions --teammate-mode in-process --verbose",
    "safe": "--verbose",
    SHELL_MODE: "",
}

# Permission mode per launch, replacing the RC_FLAGS strings as the source
# of truth for what sessions.build_tmux_command actually passes. RC_FLAGS
# itself stays defined above, unchanged, for any external caller still
# reading it as a flag string during a rolling upgrade.
PERMISSION_MODE = {
    "c": "bypassPermissions",
    "ci": "bypassPermissions",
    "safe": "acceptEdits",
}

# Extra argv tokens per mode beyond --permission-mode, in the same style
# tmux command lists use everywhere else in this codebase (no shell
# quoting needed - these become individual argv entries).
EXTRA_FLAGS = {
    "ci": ["--teammate-mode", "in-process"],
}

def resolve_claude_mode(mode):
    """Coerce a mode to one that actually runs Claude Code.

    The scheduler always needs a Claude session; a shell (or an unknown
    mode) would leave it driving a bare prompt.
    """
    if mode == SHELL_MODE or mode not in RC_FLAGS:
        return "c"
    return mode


MODEL_MAP = {
    "1": None,       # Default (Opus 4.8)
    "2": "sonnet",   # Sonnet 5
    "3": "haiku",    # Haiku 4.5
    "4": "fable",    # Fable 5
}

SCHEDULES_FILE = os.path.join(RC_HOME, "schedules.json")
# Registry of remote devices this hub can proxy to (see devices.py).
DEVICES_FILE = os.path.join(RC_HOME, "devices.json")
LOG_FILE = os.path.join(RC_HOME, "logs", "claude-rc.log")

# Directories the browser can navigate into. Comma-separated absolute paths.
# Supports ~ for home directory. Paths that don't exist are silently ignored.
# Default: home dir, /tmp, /var/www, and the RC installation folder.
_default_roots = f"~,/tmp,/var/www,{RC_HOME}"
BROWSE_ROOTS = [
    os.path.realpath(os.path.expanduser(p.strip()))
    for p in os.environ.get("RC_BROWSE_ROOTS", _default_roots).split(",")
    if p.strip()
]

# Ensure directories exist. RC_HOME holds credentials (env, devices.json,
# auth tokens) — keep it out of reach of other local users.
os.makedirs(RC_HOME, mode=0o700, exist_ok=True)
try:
    os.chmod(RC_HOME, 0o700)
except OSError:
    pass
os.makedirs(os.path.join(RC_HOME, "logs"), exist_ok=True)

_ENV_FILE = os.path.join(RC_HOME, "env")


def _read_hash_salt_from(path):
    try:
        with open(path) as f:
            for line in f:
                if line.startswith("RC_HASH_SALT="):
                    return line.strip().split("=", 1)[1]
    except OSError:
        pass
    return None


def _load_or_create_hash_salt():
    """RC_HASH_SALT, generated once into ~/.claude-rc/env (0600) if
    absent -- mirrors devices.py's device-name file pattern. Read order:
    explicit env var, then the persisted file, then generate fresh.

    The generate-and-append path is guarded by an flock on the env file
    so two processes starting concurrently (e.g. the launcher and a
    hook invocation on first run) can't each generate and append a
    *different* salt -- readers taking the first RC_HASH_SALT= line
    would then disagree with each other on session-id hashing depending
    on which line landed first. Only one process wins the lock and
    writes; the other blocks, then re-reads under the same lock and
    picks up what the winner wrote instead of writing its own."""
    env_var = os.environ.get("RC_HASH_SALT", "").strip()
    if env_var:
        return env_var

    existing = _read_hash_salt_from(_ENV_FILE)
    if existing:
        return existing

    import secrets
    salt = secrets.token_hex(32)
    line = f"RC_HASH_SALT={salt}\n"
    try:
        fd = os.open(_ENV_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return salt
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass  # no advisory locking available -- best effort, as before
        # Re-check under the lock: another process may have won the race
        # to create the salt between our unlocked read above and here.
        winner = _read_hash_salt_from(_ENV_FILE)
        if winner:
            return winner
        with os.fdopen(os.dup(fd), "a") as f:
            f.write(line)
        os.chmod(_ENV_FILE, 0o600)
    except OSError:
        pass
    finally:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        os.close(fd)
    return salt


RC_HASH_SALT = _load_or_create_hash_salt()
os.environ.setdefault("RC_HASH_SALT", RC_HASH_SALT)
