#!/usr/bin/env python3
"""MCP server that lets Claude read, add and remove items on the running Drafting Table app.

Claude Desktop starts this file itself (see the config snippet below). It talks to the
app's local API on 127.0.0.1, so the Drafting Table app must be open.

Claude Desktop config (Settings > Developer > Edit Config), added under "mcpServers":

    "drafting-table": {
      "command": "python3",
      "args": ["/FULL/PATH/TO/drafting_table_mcp.py"]
    }

Then restart Claude Desktop.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"
TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".drafting_table_token")
PALETTE = ["#fff3a3", "#ffd6c9", "#cfe8ff", "#d5f5dc", "#ead7ff"]


def read_token():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        raise RuntimeError("No token found. Open the Drafting Table app once so it creates one.")


def call(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={
        "Authorization": "Bearer " + read_token(),
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as err:
        try:
            msg = json.loads(err.read()).get("error", str(err))
        except Exception:
            msg = str(err)
        raise RuntimeError(msg)
    except urllib.error.URLError:
        raise RuntimeError("The Drafting Table app is not running. Open it and try again.")


# ---------- Tool implementations ----------
def t_get_board(a):
    return call("GET", "/board")


def t_add_items(a):
    return call("POST", "/items", {"items": a["items"]})


def t_delete_items(a):
    return call("POST", "/items/delete", {"ids": a["ids"]})


def t_add_brainstorm(a):
    ideas = a["ideas"]
    if not ideas:
        raise RuntimeError("Give at least one idea.")
    cols = max(1, int(a.get("columns", 4)))
    x0, y0 = float(a.get("x", 80)), float(a.get("y", 80))
    items = []
    top = y0
    if a.get("title"):
        items.append({"type": "note", "text": str(a["title"]), "x": x0, "y": y0,
                      "color": "Ink", "background": "#ffffff"})
        top = y0 + 70
    for i, idea in enumerate(ideas):
        row, col = divmod(i, cols)
        items.append({"type": "note", "text": str(idea), "x": x0 + col * 250, "y": top + row * 130,
                      "color": "Ink", "background": PALETTE[i % len(PALETTE)]})
    result = call("POST", "/items", {"items": items})
    result["layout"] = f"{len(ideas)} ideas in {cols} columns"
    return result


TOOLS = {
    "get_board": {
        "description": "List everything on the Drafting Table board: each item's id, type, position, text, "
                       "colour and whether it is pinned. Call this before deleting, so you have the ids.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": t_get_board,
    },
    "add_items": {
        "description": "Add items to the Drafting Table board. Types: 'note' (sticky text, optional "
                       "background colour), 'line' and 'arrow' (give x1,y1,x2,y2), 'highlight' and 'stroke' "
                       "(give a points list of [x, y]). Coordinates are board pixels, from 0 to 3000 across "
                       "and 0 to 2200 down. Colours: Ink, Red, Blue, Green, or hex like #aabbcc. The user can undo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "maxItems": 200,
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["note", "line", "arrow", "highlight", "stroke"]},
                            "text": {"type": "string"},
                            "x": {"type": "number"}, "y": {"type": "number"},
                            "x1": {"type": "number"}, "y1": {"type": "number"},
                            "x2": {"type": "number"}, "y2": {"type": "number"},
                            "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                            "color": {"type": "string"},
                            "background": {"type": "string", "description": "hex colour behind a note"},
                        },
                        "required": ["type"],
                    },
                }
            },
            "required": ["items"],
        },
        "handler": t_add_items,
    },
    "add_brainstorm": {
        "description": "Place a brainstorm board: a title and a grid of idea notes, laid out for you. "
                       "Use this for a list of ideas. It does not check for overlaps with existing items.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "ideas": {"type": "array", "items": {"type": "string"}, "maxItems": 60},
                "columns": {"type": "integer", "minimum": 1, "maximum": 10},
                "x": {"type": "number"}, "y": {"type": "number"},
            },
            "required": ["ideas"],
        },
        "handler": t_add_brainstorm,
    },
    "delete_items": {
        "description": "Remove items from the board by id (get ids from get_board). Pinned items are "
                       "skipped and reported; the user must unpin them first.",
        "inputSchema": {
            "type": "object",
            "properties": {"ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 200}},
            "required": ["ids"],
        },
        "handler": t_delete_items,
    },
}


# ---------- MCP stdio protocol ----------
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def reply(mid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    send(msg)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if mid is None:
            continue  # notifications need no reply
        if method == "initialize":
            reply(mid, {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "drafting-table", "version": "1.0"},
            })
        elif method == "ping":
            reply(mid, {})
        elif method == "tools/list":
            reply(mid, {"tools": [{"name": n, "description": t["description"], "inputSchema": t["inputSchema"]}
                                  for n, t in TOOLS.items()]})
        elif method == "tools/call":
            name = params.get("name")
            tool = TOOLS.get(name)
            if not tool:
                reply(mid, {"content": [{"type": "text", "text": f"Unknown tool {name}"}], "isError": True})
                continue
            try:
                out = tool["handler"](params.get("arguments") or {})
                reply(mid, {"content": [{"type": "text", "text": json.dumps(out, indent=2)}]})
            except Exception as err:
                reply(mid, {"content": [{"type": "text", "text": str(err)}], "isError": True})
        else:
            reply(mid, error={"code": -32601, "message": f"method not found: {method}"})


if __name__ == "__main__":
    main()
