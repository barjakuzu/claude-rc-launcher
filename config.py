"""Shared configuration for Claude RC Launcher."""

import os

VERSION = "1.3.3"

# Single home directory for all claude-rc data
RC_HOME = os.environ.get("RC_HOME", os.path.expanduser("~/.claude-rc"))

HOST = os.environ.get("RC_HOST", "0.0.0.0")
PORT = int(os.environ.get("RC_PORT", "8200"))
SESSION_PREFIX = os.environ.get("RC_PREFIX", "rc-")
WORKING_DIR = os.environ.get("RC_WORKING_DIR", ".")
CLAUDE_BIN = os.environ.get("RC_CLAUDE_BIN", "claude")
AUTH_USER = os.environ.get("RC_AUTH_USER", "")
AUTH_PASS = os.environ.get("RC_AUTH_PASS", "")
SHELL_BIN = os.environ.get("SHELL", "/bin/bash")

# Resolve relative working dir to absolute
WORKING_DIR = os.path.abspath(WORKING_DIR)

RC_FLAGS = {
    "c": "--dangerously-skip-permissions --verbose",
    "ci": "--dangerously-skip-permissions --teammate-mode in-process --verbose",
    "safe": "--verbose",
}

MODEL_MAP = {
    "1": None,       # Default (Opus)
    "2": "sonnet",   # Sonnet 4.6
    "3": "haiku",    # Haiku 4.5
}

SCHEDULES_FILE = os.path.join(RC_HOME, "schedules.json")
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

# Ensure directories exist
os.makedirs(os.path.join(RC_HOME, "logs"), exist_ok=True)
