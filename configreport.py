"""Per-device config parity report: git state of ~/claude-config, skills,
agents, rules, plugins, marketplaces, and settings.json — read-only,
stdlib only, degrades to None/[]/False on any failure, never raises.

Never reads the CONTENTS of any file under skills/ (only names, and
symlink health) — a device-only skill can hold a live secret."""
import hashlib
import json
import os
import re
import subprocess
import time

import compat
import config


def _run_ok(run, cmd, timeout=10):
    """Returns (ok, stdout_or_None). Never raises."""
    try:
        r = run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return False, None
    if r.returncode != 0:
        return False, None
    return True, r.stdout


def _git_cmd(cfg_dir, *args):
    return ["git", "-C", cfg_dir] + list(args)


def _unquote_git_path(name):
    """git status --porcelain quotes paths containing special/non-ASCII
    chars in double quotes with C-style backslash escapes; strip that."""
    if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
        inner = name[1:-1]
        try:
            return inner.encode("latin-1").decode("unicode_escape").encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return inner
    return name


def _dir_names(path):
    try:
        return sorted(
            n for n in os.listdir(path)
            if os.path.isdir(os.path.join(path, n)) or os.path.islink(os.path.join(path, n))
        )
    except OSError:
        return []


def _file_stems(path, suffix=".md"):
    try:
        return sorted(n[: -len(suffix)] for n in os.listdir(path) if n.endswith(suffix))
    except OSError:
        return []


def _git_state(cfg_dir, run, errors):
    state = {"path": cfg_dir, "head": None, "short_head": None, "dirty": False,
              "dirty_files": [], "behind_remote": None, "last_commit_date": None}
    if not os.path.isdir(os.path.join(cfg_dir, ".git")):
        errors.append("claude-config: not a git repo")
        return state
    ok, out = _run_ok(run, _git_cmd(cfg_dir, "rev-parse", "HEAD"))
    if ok:
        state["head"] = out.strip()
    else:
        errors.append("claude-config: git rev-parse HEAD failed")
    ok, out = _run_ok(run, _git_cmd(cfg_dir, "rev-parse", "--short", "HEAD"))
    if ok:
        state["short_head"] = out.strip()
    ok, out = _run_ok(run, _git_cmd(cfg_dir, "status", "--porcelain"))
    if ok:
        # "XY path" or "XY old -> new" for renames — the path (or new name)
        # is everything after the two-char status + one space.
        dirty_files = []
        for line in out.splitlines():
            if not line.strip():
                continue
            name = line[3:].strip()
            if " -> " in name:
                name = name.split(" -> ", 1)[1]
            dirty_files.append(_unquote_git_path(name))
        state["dirty_files"] = dirty_files
        state["dirty"] = bool(dirty_files)
    else:
        errors.append("claude-config: git status failed")
    ok, out = _run_ok(run, _git_cmd(cfg_dir, "rev-list", "--count", "HEAD..@{u}"))
    if ok and out.strip().isdigit():
        state["behind_remote"] = int(out.strip())
    ok, out = _run_ok(run, _git_cmd(cfg_dir, "log", "-1", "--format=%cI"))
    if ok:
        state["last_commit_date"] = out.strip() or None
    return state


def _device_only_names_from_gitignore(cfg_dir, errors):
    path = os.path.join(cfg_dir, ".gitignore")
    if not os.path.isfile(path):
        return []
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        errors.append(".gitignore: read failed")
        return []
    names = []
    pattern = re.compile(r"^/skills/([^/]+)/?$")
    for line in lines:
        m = pattern.match(line.strip())
        if m:
            names.append(m.group(1))
    return sorted(set(names))


def _broken_link_targets_external(cfg_dir, link_path):
    external_prefix = os.path.join(cfg_dir, "external") + os.sep
    try:
        target = os.readlink(link_path)
    except OSError:
        return False
    if not os.path.isabs(target):
        target = os.path.normpath(os.path.join(os.path.dirname(link_path), target))
    return (target + os.sep).startswith(external_prefix) or target == os.path.join(cfg_dir, "external")


def _skills_report(cfg_dir, errors):
    skills_dir = os.path.join(cfg_dir, "skills")
    names = _dir_names(skills_dir)
    gitignore_names = set(_device_only_names_from_gitignore(cfg_dir, errors))
    device_only = gitignore_names & set(names)
    dangling, deps_missing = [], []
    for n in names:
        entry = os.path.join(skills_dir, n)
        broken_links = []
        if os.path.islink(entry):
            if not os.path.exists(entry):
                broken_links.append(entry)
        else:
            # followlinks=False: a self-referential/cyclic symlink inside a
            # skill dir must never cause an infinite walk. os.walk never
            # reports a symlinked dir as a "dir" to descend into when
            # followlinks is False, but a broken symlink is still surfaced
            # as a file-like entry in `files` (os.path.isdir on a broken
            # link is always False), so broken-link detection is unaffected.
            for root, _dirs, files in os.walk(entry, followlinks=False):
                for f in files:
                    full = os.path.join(root, f)
                    if os.path.islink(full) and not os.path.exists(full):
                        broken_links.append(full)
        if not broken_links:
            continue
        if any(_broken_link_targets_external(cfg_dir, lk) for lk in broken_links):
            deps_missing.append(n)
        else:
            dangling.append(n)
    return {
        "count": len(names), "names": names,
        "dangling": sorted(set(dangling)),
        "deps_missing": sorted(set(deps_missing)),
        "device_only": sorted(device_only),
    }


def _plugins_report(cfg_dir, run, errors):
    declared = []
    plugins_txt = os.path.join(cfg_dir, "plugins.txt")
    if os.path.isfile(plugins_txt):
        try:
            with open(plugins_txt) as f:
                declared = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        except OSError:
            errors.append("plugins.txt: read failed")
    installed = []
    installed_status = {}
    plugin_entry_re = re.compile(r"^[\w.-]+@[\w.-]+$")
    bullet_chars = "❯>-*•"
    ok, out = _run_ok(run, [config.CLAUDE_BIN, "plugin", "list"])
    if ok:
        current = None  # entry name awaiting a possible Status: line
        for line in out.splitlines():
            stripped = line.strip().lstrip(bullet_chars).strip()
            if not stripped:
                continue
            if stripped.lower().startswith("status:"):
                if current is not None:
                    status_val = stripped.split(":", 1)[1].strip()
                    installed_status[current] = "✔" in status_val or "enabled" in status_val.lower()
                    current = None
                continue
            entry = stripped.split()[0]
            if not plugin_entry_re.match(entry):
                continue
            marketplace = entry.split("@", 1)[1]
            if marketplace == "skills-dir":
                current = None
                continue  # synthetic marketplace for ~/.claude/skills entries; always undeclared by design
            installed.append(entry)
            installed_status[entry] = True  # default until/unless a Status: line overrides it
            current = entry
    else:
        errors.append("claude plugin list: failed")
    declared_set, installed_set = set(declared), set(installed)
    return {
        "declared": declared, "installed": installed, "installed_status": installed_status,
        "disabled": sorted(name for name, enabled in installed_status.items() if not enabled),
        "missing": sorted(declared_set - installed_set),
        "extra": sorted(installed_set - declared_set),
    }


def _marketplaces(cfg_dir, errors):
    path = os.path.join(cfg_dir, "marketplaces.txt")
    if not os.path.isfile(path):
        return []
    try:
        with open(path) as f:
            return [ln.split()[0] for ln in f if ln.strip() and not ln.startswith("#")]
    except OSError:
        errors.append("marketplaces.txt: read failed")
        return []


def _read_json_object(path, errors, label):
    """Reads `path` as a JSON object. Returns (ok, dict). Never raises;
    a missing file, unreadable file, invalid JSON, or non-object JSON all
    yield ok=False without raising."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        errors.append(f"{label}: read failed")
        return False, {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        errors.append(f"{label}: could not parse as JSON")
        return False, {}
    if not isinstance(data, dict):
        errors.append(f"{label}: not a JSON object")
        return False, {}
    return True, data


def _base_sync(cfg_dir, home, errors):
    """Compares the repo's authoritative `config/settings.base.json` against
    the device's real, untracked `~/.claude/settings.json`. Reports only
    top-level KEY NAMES, never values — settings.json can hold an `env`
    block with live secrets. Keys present only on the device (e.g. `model`,
    `effortLevel`, `modelSettings`, written by the desktop app / `/model` /
    `/effort`) are expected and never reported. Never raises: any read or
    parse failure on either side degrades to `unknown`."""
    base_path = os.path.join(cfg_dir, "config", "settings.base.json")
    device_path = os.path.join(home, ".claude", "settings.json")
    base_ok, base = _read_json_object(base_path, errors, "settings.base.json")
    device_ok, device = _read_json_object(device_path, errors, "~/.claude/settings.json")
    if not base_ok or not device_ok:
        return {"kind": "unknown", "missing": [], "differing": []}
    missing, differing = [], []
    for key, value in base.items():
        if key not in device:
            missing.append(key)
        elif device[key] != value:
            differing.append(key)
    missing.sort()
    differing.sort()
    kind = "stale" if (missing or differing) else "in-sync"
    return {"kind": kind, "missing": missing, "differing": differing}


def _settings_report(cfg_dir, home, errors):
    # hooks_present / remote_control_at_startup / sha256 describe the
    # repo's shared, authoritative settings.base.json (config/settings.json
    # no longer exists — it was split into settings.base.json, merged over
    # the device file on every bootstrap, and settings.seed.json, applied
    # only when a key is absent).
    path = os.path.join(cfg_dir, "config", "settings.base.json")
    result = {"hooks_present": False, "remote_control_at_startup": None,
              "skills_symlinked": False, "sha256": None}
    if os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                raw = f.read()
            result["sha256"] = hashlib.sha256(raw).hexdigest()
            base_data = json.loads(raw)
            result["hooks_present"] = bool(base_data.get("hooks"))
            result["remote_control_at_startup"] = base_data.get("remoteControlAtStartup")
        except (OSError, ValueError) as e:
            errors.append(f"settings.base.json: {e}")
    # skills_symlinked: is ~/.claude/skills a symlink into
    # claude-config/skills (a proper linked install rather than a copy).
    # ~/.claude/settings.json is no longer a symlink by design (it is a
    # real, device-owned file), so there is nothing to report there.
    result["skills_symlinked"] = os.path.islink(os.path.join(home, ".claude", "skills"))
    return result


def _effective_model(home, errors):
    """`model` is read from the device's own ~/.claude/settings.json —
    never from the repo, and not primarily from the environment (a
    device's own model is not drift). ANTHROPIC_MODEL, when set, genuinely
    overrides at runtime and is reported separately as env_model_override."""
    device_path = os.path.join(home, ".claude", "settings.json")
    ok, device = _read_json_object(device_path, errors, "~/.claude/settings.json (effective_model)")
    model = device.get("model") if ok else None
    env_model = os.environ.get("ANTHROPIC_MODEL")
    return model, env_model


def collect_config_report(home=None, run=subprocess.run):
    home = home or os.path.expanduser("~")
    cfg_dir = os.path.join(home, "claude-config")
    errors = []

    claude_version = None
    try:
        claude_version = compat.claude_version()
    except Exception:
        errors.append("compat.claude_version() failed")

    settings_report = _settings_report(cfg_dir, home, errors)
    git_state = _git_state(cfg_dir, run, errors)
    settings_report["base_sync"] = _base_sync(cfg_dir, home, errors)

    model, env_model = _effective_model(home, errors)
    result = {
        "claude_version": claude_version,
        "launcher_version": config.VERSION,
        "claude_config": git_state,
        "skills": _skills_report(cfg_dir, errors),
        "agents": _file_stems(os.path.join(cfg_dir, "agents")),
        "rules": {
            "shared": _file_stems(os.path.join(cfg_dir, "rules")),
            "local": _file_stems(os.path.join(cfg_dir, "rules", "local")),
        },
        "plugins": _plugins_report(cfg_dir, run, errors),
        "marketplaces": _marketplaces(cfg_dir, errors),
        "settings": settings_report,
        "effective_model": model,
        "claude_local_md": os.path.isfile(os.path.join(home, ".claude", "CLAUDE.local.md")),
        "generated_at": int(time.time()),
        "errors": errors,
    }
    if env_model:
        result["env_model_override"] = env_model
    return result
