"""Remote device registry.

The hub (this machine) is the implicit "local" device — it is never listed
here and never proxied. Each entry in devices.json describes another machine
running its own rc-launcher app, reachable over a private network (Tailscale):

    [
      {
        "id": "home",
        "name": "Home Ubuntu",
        "base_url": "http://home-box.example.ts.net:8200",
        "auth_user": "admin",
        "auth_pass": "..."
      }
    ]

The file holds per-device credentials, so it should be chmod 600. Credentials
are used server-side only (the proxy injects them) and are never sent to the
browser — see list_devices_public().
"""

import json
import os
import socket

from config import DEVICES_FILE, RC_HOME

# The local device's display name lives in a tiny state file so it can be
# renamed from the UI. Falls back to RC_DEVICE_NAME env, then hostname.
LOCAL_NAME_FILE = os.path.join(RC_HOME, "device-name")


def get_local_name():
    """Display name for this machine (the implicit 'local' device)."""
    try:
        with open(LOCAL_NAME_FILE) as f:
            name = f.read().strip()
        if name:
            return name
    except OSError:
        pass
    env_name = os.environ.get("RC_DEVICE_NAME", "").strip()
    if env_name:
        return env_name
    return f"This machine ({socket.gethostname()})"


def set_local_name(name):
    with open(LOCAL_NAME_FILE, "w") as f:
        f.write(name.strip() + "\n")


def rename_device(device_id, name):
    """Rename a device. 'local'/empty renames this machine; otherwise the
    matching entry in devices.json is updated. Returns (ok, message)."""
    name = (name or "").strip()
    if not name:
        return False, "Name cannot be empty"
    if len(name) > 60:
        return False, "Name too long (max 60 chars)"
    if not device_id or device_id == "local":
        set_local_name(name)
        return True, "Renamed"
    try:
        with open(DEVICES_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return False, "No device registry"
    if not isinstance(data, list):
        return False, "Invalid device registry"
    for d in data:
        if isinstance(d, dict) and d.get("id") == device_id:
            d["name"] = name
            # Create with 0600 atomically (file holds device credentials).
            fd = os.open(DEVICES_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.chmod(DEVICES_FILE, 0o600)
            return True, "Renamed"
    return False, f"Unknown device: {device_id}"


def load_devices():
    """Load the device registry. Returns [] if the file is missing or invalid."""
    try:
        with open(DEVICES_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return []
    if not isinstance(data, list):
        return []
    devices = []
    for d in data:
        if isinstance(d, dict) and d.get("id") and d.get("base_url"):
            devices.append(d)
    return devices


def get_device(device_id):
    """Return the device dict for device_id, or None for local/unknown.

    None and "local" both mean "this machine" (handled without proxying).
    """
    if not device_id or device_id == "local":
        return None
    for d in load_devices():
        if d["id"] == device_id:
            return d
    return None


def list_devices_public():
    """Return [{id, name}] for the UI switcher, without credentials."""
    return [
        {"id": d["id"], "name": d.get("name", d["id"])}
        for d in load_devices()
    ]
