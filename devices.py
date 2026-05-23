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

from config import DEVICES_FILE


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
