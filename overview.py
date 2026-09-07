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
    launcher_sess = [s for s in sess if not s.get("external")]
    tokens = sum(int(s.get("tokens", 0)) for s in sess)
    load_pct = 0
    os_name, spark = "", []
    user, home_dir = "", ""
    if stats:
        cores = max(1, int(stats.get("cores", 1)))
        load1 = (stats.get("loadavg") or [0])[0]
        load_pct = min(100, round((load1 / cores) * 100))
        os_name = stats.get("os", "")
        spark = stats.get("token_history") or []
        user = stats.get("user", "") or ""
        home_dir = stats.get("home_dir", "") or ""
    host = urlparse(device.get("base_url", "")).hostname or device.get("base_url", "")
    return {
        "id": device["id"], "name": device.get("name", device["id"]),
        "online": online, "hostname": host,
        "sessions": len(launcher_sess), "tokens": tokens,
        "loadPct": load_pct, "os": os_name, "spark": spark,
        "user": user, "home_dir": home_dir,
        "claude_version": (stats or {}).get("claude_version"),
        "version": (stats or {}).get("version"),
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


def fetch_config_report(device):
    try:
        return _fetch(device["base_url"], "/rc/config-report",
                       device.get("auth_user", ""), device.get("auth_pass", ""))
    except Exception:
        return None


def _derive_skew(report, hub_head, hub_version, hub_launcher_version=None):
    if report is None or "error" in report:
        return ["unreachable"]
    reasons = []
    cfg = report.get("claude_config") or {}
    head = cfg.get("head")
    if head and hub_head and head != hub_head:
        reasons.append("head differs from hub")
    if cfg.get("dirty"):
        reasons.append("dirty")
    base_sync_kind = ((report.get("settings") or {}).get("base_sync") or {}).get("kind")
    if base_sync_kind == "stale":
        reasons.append("settings out of date (run bootstrap)")
    if (report.get("skills") or {}).get("deps_missing"):
        reasons.append("external skills not installed (run bootstrap)")
    plugins = report.get("plugins") or {}
    if plugins.get("missing"):
        reasons.append("missing plugins")
    if set(plugins.get("declared") or []) & set(plugins.get("disabled") or []):
        reasons.append("plugins installed but disabled")
    if not (report.get("settings") or {}).get("hooks_present", True):
        reasons.append("no hooks")
    version = report.get("claude_version")
    if version and hub_version and version != hub_version:
        reasons.append("claude version differs")
    launcher_version = report.get("launcher_version")
    if launcher_version and hub_launcher_version and launcher_version != hub_launcher_version:
        reasons.append("launcher version differs")
    return reasons


def build_config_matrix(hub_report, devices, fetch=fetch_config_report):
    hub_head = (hub_report.get("claude_config") or {}).get("head")
    hub_version = hub_report.get("claude_version")
    hub_launcher_version = hub_report.get("launcher_version")
    reports = {"local": hub_report}
    if devices:
        with ThreadPoolExecutor(max_workers=min(8, len(devices))) as ex:
            fetched = list(ex.map(fetch, devices))
        for device, rpt in zip(devices, fetched):
            reports[device["id"]] = rpt if rpt is not None else {"error": "unreachable"}
    skew = {
        dev_id: _derive_skew(rpt, hub_head, hub_version, hub_launcher_version)
        for dev_id, rpt in reports.items()
    }
    return {"devices": reports, "hub_head": hub_head, "skew": skew}
