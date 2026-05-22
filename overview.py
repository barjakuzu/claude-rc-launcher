"""Hub-side aggregation: combine each device's sessions + stats into grid cards."""

import base64, json, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse


def card_from_parts(device, sessions, stats, online=None):
    """Build one grid card. sessions/stats are None when the device is unreachable.

    online: explicit reachability. When None, falls back to stats-based default
    (online = stats is not None) for backward compatibility.
    """
    if online is None:
        online = stats is not None
    sess = sessions or []
    tokens = sum(int(s.get("tokens", 0)) for s in sess)
    load_pct = 0
    os_name, spark = "", []
    if stats:
        cores = max(1, int(stats.get("cores", 1)))
        load1 = (stats.get("loadavg") or [0])[0]
        load_pct = min(100, round((load1 / cores) * 100))
        os_name = stats.get("os", "")
        spark = stats.get("token_history") or []
    host = urlparse(device.get("base_url", "")).hostname or device.get("base_url", "")
    return {
        "id": device["id"], "name": device.get("name", device["id"]),
        "online": online, "hostname": host,
        "sessions": len(sess), "tokens": tokens,
        "loadPct": load_pct, "os": os_name, "spark": spark,
    }


def _fetch(base_url, path, auth_user, auth_pass, timeout=3):
    req = urllib.request.Request(base_url.rstrip("/") + path)
    if auth_user or auth_pass:
        tok = base64.b64encode(f"{auth_user}:{auth_pass}".encode()).decode()
        req.add_header("Authorization", f"Basic {tok}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_remote_card(device):
    try:
        sess = _fetch(device["base_url"], "/rc/sessions", device.get("auth_user", ""), device.get("auth_pass", ""))
    except Exception:
        return card_from_parts(device, None, None, online=False)
    try:
        st = _fetch(device["base_url"], "/rc/stats", device.get("auth_user", ""), device.get("auth_pass", ""))
    except Exception:
        st = None
    return card_from_parts(device, sess.get("sessions", []), st, online=True)


def build_overview(local_device, local_sessions, local_stats, remote_devices):
    cards = [card_from_parts(local_device, local_sessions, local_stats)]
    if remote_devices:
        with ThreadPoolExecutor(max_workers=min(8, len(remote_devices))) as ex:
            cards += list(ex.map(fetch_remote_card, remote_devices))
    return cards
