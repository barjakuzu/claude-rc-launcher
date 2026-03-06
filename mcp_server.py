#!/usr/bin/env python3
"""MCP server for chat-based schedule management.

Implements the MCP stdio protocol (JSON-RPC over stdin/stdout) using only
Python stdlib. Talks to the Claude RC Launcher HTTP API on localhost.

Usage:
    Add to ~/.claude/settings.json:
    {
      "mcpServers": {
        "claude-rc-scheduler": {
          "command": "python3",
          "args": ["/path/to/mcp_server.py"],
          "env": {
            "RC_PORT": "8200",
            "RC_AUTH_USER": "...",
            "RC_AUTH_PASS": "..."
          }
        }
      }
    }
"""

import base64
import json
import os
import sys
import urllib.request
import urllib.error

RC_PORT = os.environ.get("RC_PORT", "8200")
RC_AUTH_USER = os.environ.get("RC_AUTH_USER", "")
RC_AUTH_PASS = os.environ.get("RC_AUTH_PASS", "")
BASE_URL = f"http://localhost:{RC_PORT}/rc"

TOOLS = [
    {
        "name": "list_schedules",
        "description": "List all scheduled tasks configured in the Claude RC Launcher. Returns schedule names, cron expressions, prompts, and next run times.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "create_schedule",
        "description": "Create a new scheduled task. The task will spawn a Claude Code session at the specified cron schedule with the given prompt/instructions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for this scheduled task"},
                "cron": {"type": "string", "description": "Cron expression (5 fields: minute hour day-of-month month day-of-week). Example: '0 9 * * 1-5' for weekdays at 9am"},
                "prompt": {"type": "string", "description": "Instructions for what Claude should do when this task runs"},
                "workdir": {"type": "string", "description": "Working directory for the Claude session"},
                "mode": {"type": "string", "description": "Launch mode: 'c' (skip permissions), 'ci' (teammate), 'safe' (with permissions)", "default": "c"},
                "instructions_file": {"type": "string", "description": "Optional path to a file containing detailed instructions (overrides prompt)"},
            },
            "required": ["name", "cron", "prompt", "workdir"],
        },
    },
    {
        "name": "update_schedule",
        "description": "Update an existing scheduled task. You can change any field including enabling/disabling it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Schedule ID to update"},
                "name": {"type": "string", "description": "New name"},
                "cron": {"type": "string", "description": "New cron expression"},
                "prompt": {"type": "string", "description": "New prompt/instructions"},
                "workdir": {"type": "string", "description": "New working directory"},
                "mode": {"type": "string", "description": "New launch mode"},
                "enabled": {"type": "boolean", "description": "Enable or disable the schedule"},
                "instructions_file": {"type": "string", "description": "New instructions file path"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "delete_schedule",
        "description": "Delete a scheduled task permanently.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Schedule ID to delete"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "fire_schedule",
        "description": "Manually trigger a scheduled task to run immediately, regardless of its cron schedule.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Schedule ID to fire"},
            },
            "required": ["id"],
        },
    },
]


def _api_call(method, path, body=None):
    """Make an HTTP request to the launcher API."""
    url = BASE_URL + path
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}

    if RC_AUTH_USER and RC_AUTH_PASS:
        creds = base64.b64encode(f"{RC_AUTH_USER}:{RC_AUTH_PASS}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            return body
        except Exception:
            return {"ok": False, "message": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def _handle_tool_call(name, arguments):
    """Execute a tool and return the result text."""
    if name == "list_schedules":
        result = _api_call("GET", "/schedules")
        schedules = result.get("schedules", [])
        if not schedules:
            return "No scheduled tasks configured."
        lines = []
        for s in schedules:
            status = "enabled" if s.get("enabled") else "disabled"
            last = s.get("last_run", "never")
            next_r = s.get("next_run", "N/A")
            lines.append(
                f"- **{s['name']}** (id: {s['id']})\n"
                f"  Cron: `{s['cron']}` | Status: {status}\n"
                f"  Prompt: {s.get('prompt', '')[:100]}{'...' if len(s.get('prompt', '')) > 100 else ''}\n"
                f"  Workdir: {s.get('workdir', 'N/A')} | Mode: {s.get('mode', 'c')}\n"
                f"  Last run: {last} | Next run: {next_r}"
            )
        return "\n\n".join(lines)

    elif name == "create_schedule":
        result = _api_call("POST", "/schedules", arguments)
        if result.get("ok"):
            s = result["schedule"]
            return f"Schedule created: **{s['name']}** (id: {s['id']})\nCron: `{s['cron']}`\nEnabled: {s['enabled']}"
        return f"Failed to create schedule: {result.get('message', 'Unknown error')}"

    elif name == "update_schedule":
        result = _api_call("POST", "/schedules/update", arguments)
        if result.get("ok"):
            s = result["schedule"]
            return f"Schedule updated: **{s['name']}** (id: {s['id']})\nEnabled: {s.get('enabled', True)}"
        return f"Failed to update schedule: {result.get('message', 'Unknown error')}"

    elif name == "delete_schedule":
        result = _api_call("POST", "/schedules/delete", arguments)
        if result.get("ok"):
            return "Schedule deleted successfully."
        return f"Failed to delete schedule: {result.get('message', 'Unknown error')}"

    elif name == "fire_schedule":
        result = _api_call("POST", "/schedules/fire", arguments)
        if result.get("ok"):
            return f"Schedule fired: {result.get('message', 'Running')}"
        return f"Failed to fire schedule: {result.get('message', 'Unknown error')}"

    return f"Unknown tool: {name}"


def _send(msg):
    """Write a JSON-RPC message to stdout."""
    raw = json.dumps(msg)
    sys.stdout.write(f"Content-Length: {len(raw)}\r\n\r\n{raw}")
    sys.stdout.flush()


def _read():
    """Read a JSON-RPC message from stdin."""
    # Read headers
    headers = {}
    while True:
        line = sys.stdin.readline()
        if not line or line.strip() == "":
            break
        if ":" in line:
            key, val = line.split(":", 1)
            headers[key.strip().lower()] = val.strip()

    content_length = int(headers.get("content-length", 0))
    if content_length == 0:
        return None
    body = sys.stdin.read(content_length)
    return json.loads(body)


def main():
    """Run the MCP server (stdio transport)."""
    while True:
        msg = _read()
        if msg is None:
            break

        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "initialize":
            _send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "claude-rc-scheduler",
                        "version": "1.0.0",
                    },
                },
            })

        elif method == "notifications/initialized":
            pass  # No response needed for notifications

        elif method == "tools/list":
            _send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS},
            })

        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            try:
                result_text = _handle_tool_call(tool_name, arguments)
                _send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": result_text}],
                    },
                })
            except Exception as e:
                _send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    },
                })

        elif msg_id is not None:
            # Unknown method with an id — respond with method not found
            _send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })


if __name__ == "__main__":
    main()
