"""Shared configuration for Claude RC Launcher."""

import os

VERSION = "1.1.0"

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

SCHEDULES_FILE = os.path.join(
    os.path.expanduser("~"), ".config", "claude-rc", "schedules.json"
)
