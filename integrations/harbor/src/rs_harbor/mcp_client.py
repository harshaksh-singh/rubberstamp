#!/usr/bin/env python3
"""Play a fixed list of tool calls against an MCP server over streamable HTTP, with the standard library only.

The generator copies this file into every task's solution/ folder: Harbor's oracle agent runs solution/solve.sh in
the agent's container, which has Python and nothing else, and solve.sh runs this on solution/plan.json. The plan
agent (`rs_harbor.agents.PlanReplayAgent`) uploads the same file with another plan. Tool errors are reported and do
not stop the run (a refused call is part of an episode); a transport or protocol error exits 1.

    python3 mcp_client.py --url http://helpdesk:8000/mcp --plan plan.json
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

PROTOCOL_VERSION = "2025-06-18"  # asked for; the server's answer is used from then on
CLIENT = {"name": "rs-harbor-plan", "version": "0.1.0"}


class McpError(RuntimeError):
    pass


class Client:
    def __init__(self, url: str, timeout: float = 60.0) -> None:
        self.url = url
        self.timeout = timeout
        self.session: str | None = None
        self.version: str | None = None
        self.ids = 0

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.version:
            headers["MCP-Protocol-Version"] = self.version
        return headers

    def _send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        data = json.dumps(message).encode("utf-8")
        request = urllib.request.Request(self.url, data=data, headers=self._headers(), method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            self.session = response.headers.get("mcp-session-id") or self.session
            content_type = response.headers.get("content-type", "")
            body = response.read().decode("utf-8")
        if "id" not in message:  # a notification: no response body to read
            return None
        replies = _sse_messages(body) if content_type.startswith("text/event-stream") else [json.loads(body)]
        for reply in replies:
            if reply.get("id") == message["id"]:
                if "error" in reply:
                    raise McpError(f"{message['method']}: {reply['error']}")
                return reply["result"]
        raise McpError(f"{message['method']}: no response with id {message['id']}")

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.ids += 1
        result = self._send({"jsonrpc": "2.0", "id": self.ids, "method": method, "params": params})
        assert result is not None
        return result

    def initialize(self, wait_s: float = 60.0) -> dict[str, Any]:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                params = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT}
                result = self.request("initialize", params)
                break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        self.version = result.get("protocolVersion", PROTOCOL_VERSION)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        return self.request("tools/list", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        if not self.session:
            return
        request = urllib.request.Request(self.url, headers=self._headers(), method="DELETE")
        # ending the session is a courtesy: the episode is already recorded
        with contextlib.suppress(urllib.error.URLError, ConnectionError, TimeoutError):
            urllib.request.urlopen(request, timeout=self.timeout).close()


def _sse_messages(body: str) -> list[dict[str, Any]]:
    """The JSON-RPC messages in a text/event-stream body (one per event, data lines joined)."""
    messages, data = [], []
    for line in [*body.splitlines(), ""]:
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif not line and data:
            messages.append(json.loads("\n".join(data)))
            data = []
    return messages


def play(url: str, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every call in order; returns one {"tool", "is_error", "result"} per call."""
    client = Client(url)
    client.initialize()
    out = []
    try:
        for call in calls:
            result = client.call_tool(call["tool"], call.get("args", {}))
            out.append({"tool": call["tool"], "is_error": bool(result.get("isError")), "result": result})
    finally:
        client.close()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Play a plan's tool calls against an MCP server.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--plan", required=True, help='a JSON file with {"calls": [{"tool": ..., "args": {...}}]}')
    args = parser.parse_args(argv)
    with open(args.plan, encoding="utf-8") as handle:
        plan = json.load(handle)
    try:
        played = play(args.url, plan["calls"])
    except (McpError, urllib.error.URLError, ConnectionError, TimeoutError, ValueError) as exc:
        print(f"mcp_client: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for entry in played:
        print(json.dumps(entry, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
