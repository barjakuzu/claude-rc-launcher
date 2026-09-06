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
        # A dirty config/settings.json alone is install-ordering, not drift
        # (plugin installs rewrite it) — only flag `dirty` when something
        # else changed too.
        state["dirty"] = any(f != "config/settings.json" for f in dirty_files)
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
    device_only = set(_device_only_names_from_gitignore(cfg_dir, errors))
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
    plugin_entry_re = re.compile(r"^[\w.-]+@[\w.-]+$")
    ok, out = _run_ok(run, ["claude", "plugin", "list"])
    if ok:
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            entry = line.split()[0]
            if not plugin_entry_re.match(entry):
                continue
            marketplace = entry.split("@", 1)[1]
            if marketplace == "skills-dir":
                continue  # synthetic marketplace for ~/.claude/skills entries; always undeclared by design
            installed.append(entry)
    else:
        errors.append("claude plugin list: failed")
    declared_set, installed_set = set(declared), set(installed)
    return {
        "declared": declared, "installed": installed,
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


def _settings_report(cfg_dir, home, errors):
    path = os.path.join(cfg_dir, "config", "settings.json")
    result = {"hooks_present": False, "remote_control_at_startup": None,
              "settings_symlinked": False, "skills_symlinked": False, "sha256": None}
    settings_data = {}
    if os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                raw = f.read()
            result["sha256"] = hashlib.sha256(raw).hexdigest()
            settings_data = json.loads(raw)
            result["hooks_present"] = bool(settings_data.get("hooks"))
            result["remote_control_at_startup"] = settings_data.get("remoteControlAtStartup")
        except (OSError, ValueError) as e:
            errors.append(f"settings.json: {e}")
    # settings_symlinked: is the live ~/.claude/settings.json itself a
    # symlink (into claude-config/config/settings.json).
    # skills_symlinked: is ~/.claude/skills a symlink into
    # claude-config/skills (a proper linked install rather than a copy).
    result["settings_symlinked"] = os.path.islink(os.path.join(home, ".claude", "settings.json"))
    result["skills_symlinked"] = os.path.islink(os.path.join(home, ".claude", "skills"))
    return result, settings_data


def _effective_model(settings_data):
    env_model = os.environ.get("ANTHROPIC_MODEL")
    if env_model:
        return env_model
    return settings_data.get("model")


def collect_config_report(home=None, run=subprocess.run):
    home = home or os.path.expanduser("~")
    cfg_dir = os.path.join(home, "claude-config")
    errors = []

    claude_version = None
    try:
        claude_version = compat.claude_version()
    except Exception:
        errors.append("compat.claude_version() failed")

    settings_report, settings_data = _settings_report(cfg_dir, home, errors)

    return {
        "claude_version": claude_version,
        "claude_config": _git_state(cfg_dir, run, errors),
        "skills": _skills_report(cfg_dir, errors),
        "agents": _file_stems(os.path.join(cfg_dir, "agents")),
        "rules": {
            "shared": _file_stems(os.path.join(cfg_dir, "rules")),
            "local": _file_stems(os.path.join(cfg_dir, "rules", "local")),
        },
        "plugins": _plugins_report(cfg_dir, run, errors),
        "marketplaces": _marketplaces(cfg_dir, errors),
        "settings": settings_report,
        "effective_model": _effective_model(settings_data),
        "claude_local_md": os.path.isfile(os.path.join(home, ".claude", "CLAUDE.local.md")),
        "generated_at": int(time.time()),
        "errors": errors,
    }
