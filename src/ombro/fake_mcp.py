"""A fake MCP server, for tests and dry runs. It reaches nothing outside.

Run as `ombro fake-mcp` (or `python -m ombro.fake_mcp`). It speaks MCP over stdin and stdout and
offers invented tools, one of each class:

- find_rows    read    (marked read-only)
- add_row      change  (marked as not destructive)
- post_message send    (not marked, so Ombro treats it as send)
- fire_hook    send    (no arguments, like a webhook)

Every tools/call is appended, as one JSON line, to the file named by
OMBRO_FAKE_MCP_RECORD (default: fake-mcp-sent.jsonl in the Ombro home):
what the server would have done. Other settings, for
tests: OMBRO_FAKE_MCP_SLEEP (seconds to wait inside each call, after
recording it), OMBRO_FAKE_MCP_FAIL (a tool name whose calls return an error),
OMBRO_FAKE_MCP_ERA (`legacy`, the default: the 2025-06-18 handshake; or
`modern`: 2026-07-28, no handshake, every request carrying its version).
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

TOOLS = [
    {
        "name": "find_rows",
        "description": "Find rows in an invented sheet that contain some words.",
        "inputSchema": {"type": "object", "properties": {"sheet": {"type": "string"}, "query": {"type": "string"}},
                        "required": ["sheet"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "add_row",
        "description": "Add one row to an invented sheet.",
        "inputSchema": {
            "type": "object",
            "properties": {"sheet": {"type": "string"}, "values": {"type": "array", "items": {"type": "string"}}},
            "required": ["sheet", "values"],
        },
        # Internal: the server says it reaches nothing outside the sheet.
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "post_message",
        "description": "Post a message to an invented channel.",
        "inputSchema": {
            "type": "object",
            "properties": {"channel": {"type": "string"}, "text": {"type": "string", "maxLength": 280}},
            "required": ["channel", "text"],
        },
    },
    {
        "name": "fire_hook",
        "description": "Fire an invented webhook. It takes no arguments.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

# With OMBRO_FAKE_MCP_EXTRA=trash, a tool the server says stays inside but that deletes (for the approval tests).
EXTRA = [
    {
        "name": "trash_sheet",
        "description": "Move an invented sheet to the bin.",
        "inputSchema": {"type": "object", "properties": {"sheet": {"type": "string"}}, "required": ["sheet"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
    },
]


def tools() -> list[dict]:
    return TOOLS + (EXTRA if os.environ.get("OMBRO_FAKE_MCP_EXTRA") == "trash" else [])


def _record(entry: dict) -> None:
    path = os.environ.get("OMBRO_FAKE_MCP_RECORD")
    if not path:
        from ombro.home import home_root

        path = str(home_root() / "fake-mcp-sent.jsonl")
    entry = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **entry}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()


MODERN = "2026-07-28"


def _call(name: str, args: dict, protocol: str = "2025-06-18") -> dict:
    if name not in {t["name"] for t in tools()}:
        return {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}
    _record({"tool": name, "arguments": args, "protocol": protocol})
    if os.environ.get("OMBRO_FAKE_MCP_SLEEP"):
        time.sleep(float(os.environ["OMBRO_FAKE_MCP_SLEEP"]))
    if os.environ.get("OMBRO_FAKE_MCP_FAIL") == name:
        return {"content": [{"type": "text", "text": f"{name} failed (an invented failure)"}], "isError": True}
    if name == "find_rows":
        text = json.dumps([{"row": 2, "values": ["Invented Ltd", "Lowtown"]}])
    elif name == "add_row":
        text = f"Would have added a row to {args.get('sheet')}: {args.get('values')}"
    elif name == "post_message":
        text = f"Would have posted to {args.get('channel')}: {args.get('text')}"
    else:
        text = "Would have fired the hook."
    return {"content": [{"type": "text", "text": text}]}


def _error(rid, code: int, message: str, data=None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


def handle_modern(msg: dict) -> dict | None:
    """2026-07-28: no handshake; each request carries its version in _meta."""
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        return None
    params = msg.get("params") or {}
    meta = params.get("_meta") or {}
    version = meta.get("io.modelcontextprotocol/protocolVersion")
    if method == "initialize" or version != MODERN:
        return _error(rid, -32022, "Unsupported protocol version",
                      {"supported": [MODERN], "requested": version or params.get("protocolVersion")})
    if "io.modelcontextprotocol/clientCapabilities" not in meta:
        return _error(rid, -32602, "missing clientCapabilities")
    if method == "server/discover":
        result = {"supportedVersions": [MODERN], "capabilities": {"tools": {}},
                  "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "ombro-fake", "version": "1"}},
                  "ttlMs": 0, "cacheScope": "public"}
    elif method == "tools/list":
        result = {"tools": tools(), "ttlMs": 0, "cacheScope": "public"}
    elif method == "tools/call":
        result = _call(str(params.get("name")), params.get("arguments") or {}, MODERN)
    else:
        return _error(rid, -32601, f"no method {method}")
    return {"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", **result}}


def handle(msg: dict) -> dict | None:
    if os.environ.get("OMBRO_FAKE_MCP_ERA") == "modern":
        return handle_modern(msg)
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        return None  # a notification
    if method == "initialize":
        result = {
            "protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ombro-fake", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": tools()}
    elif method == "tools/call":
        p = msg.get("params") or {}
        result = _call(str(p.get("name")), p.get("arguments") or {})
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"no method {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        reply = handle(msg) if isinstance(msg, dict) else None
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
