#!/usr/bin/env python3
"""Retrieve Jira tickets via wtmcp MCP server."""

# /// script
# dependencies = [
#     "requests>=2.31.0",
# ]
# ///

import csv
import io
import json
import logging
import re
import sys
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

WTMCP_BASE_URL = "http://localhost:8080/mcp"

_request_id = 0
_session_id: Optional[str] = None


def _next_id() -> int:
    global _request_id
    _request_id += 1
    return _request_id


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if _session_id:
        h["mcp-session-id"] = _session_id
    return h


def connect(base_url: str = WTMCP_BASE_URL) -> bool:
    """Initialize MCP session with wtmcp. Returns True on success."""
    global _session_id, WTMCP_BASE_URL
    _session_id = None  # always start a fresh session
    WTMCP_BASE_URL = base_url
    try:
        payload = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "retrieve_jira_tickets", "version": "1.0"},
            },
            "id": _next_id(),
        }
        response = requests.post(
            WTMCP_BASE_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        if response.status_code == 200:
            _session_id = response.headers.get("mcp-session-id")
            logger.info("Connected to wtmcp at %s (session: %s)", WTMCP_BASE_URL, _session_id)
            return True
        logger.error("wtmcp returned status %s", response.status_code)
        return False
    except requests.exceptions.ConnectionError:
        logger.error("Failed to connect to wtmcp at %s", WTMCP_BASE_URL)
        return False
    except Exception as e:
        logger.error("Error connecting to wtmcp: %s", e)
        return False


def call_tool(tool_name: str, arguments: dict, timeout: int = 30, _retries: int = 3) -> Optional[str]:
    """Call an MCP tool. Returns raw text content or None on error.
    Retries automatically on rate-limit responses."""
    try:
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
            "id": _next_id(),
        }
        response = requests.post(
            WTMCP_BASE_URL,
            json=payload,
            headers=_headers(),
            timeout=timeout,
        )
        if response.status_code != 200:
            logger.error("Tool call failed with status %s: %s", response.status_code, response.text)
            return None
        data = response.json()
        if "error" in data:
            logger.error("MCP error: %s", data["error"])
            return None
        content = data.get("result", {}).get("content", [])
        if not content:
            logger.error("Empty content in MCP response")
            return None
        text = content[0].get("text", "")
        # Handle MCP rate-limit: "rate limited — retry after Nms"
        m = re.match(r"rate limited\D+(\d+)\s*ms", text, re.IGNORECASE)
        if m:
            wait = int(m.group(1)) / 1000.0 + 0.1
            logger.warning("Rate limited by MCP; retrying in %.2fs (%d retries left)", wait, _retries)
            if _retries > 0:
                time.sleep(wait)
                return call_tool(tool_name, arguments, timeout=timeout, _retries=_retries - 1)
            logger.error("Rate limit retries exhausted for %s", tool_name)
            return None
        return text
    except Exception as e:
        logger.error("Error calling tool %s: %s", tool_name, e)
        return None


def _parse_tool_result(text: str) -> list[dict]:
    """Parse wtmcp tool-result text format into a list of dicts."""
    # Strip the <tool-result-...> wrapper tags
    inner = re.sub(r"</?tool-result[^>]*>", "", text).strip()
    lines = inner.splitlines()

    columns: list[str] = []
    rows: list[str] = []

    for line in lines:
        # Header line: issues[N]{col1,col2,...}:
        m = re.match(r"issues\[\d+\]\{([^}]+)\}:", line.strip())
        if m:
            columns = [c.strip() for c in m.group(1).split(",")]
            continue
        # Skip metadata lines (count:, start_at:, total:)
        if re.match(r"\w+:\s", line) or not line.strip():
            continue
        rows.append(line.strip())

    if not columns or not rows:
        return []

    reader = csv.reader(io.StringIO("\n".join(rows)), skipinitialspace=True)
    return [dict(zip(columns, row)) for row in reader if row]


def search_jira(jql: str, max_results: int = 100, fields: Optional[list[str]] = None) -> list[dict]:
    """
    Search Jira using a JQL query via wtmcp.

    Args:
        jql: JQL query string
        max_results: Maximum number of results to return
        fields: List of field names to include in the response (e.g. ["key", "summary", "status"])

    Returns:
        List of ticket dicts with keys matching the response columns
    """
    logger.info("Searching Jira: %s", jql)
    arguments: dict = {"jql": jql, "max_results": max_results}
    if fields:
        arguments["fields"] = ",".join(fields)
    text = call_tool("jira_search", arguments)
    if text is None:
        return []
    tickets = _parse_tool_result(text)
    logger.info("Retrieved %d tickets", len(tickets))
    return tickets


def print_tickets(tickets: list[dict]) -> None:
    if not tickets:
        print("No tickets found.")
        return
    print(f"\nFound {len(tickets)} tickets:\n")
    print("-" * 80)
    for ticket in tickets:
        for k, v in ticket.items():
            print(f"{k}: {v}")
        print("-" * 80)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    import argparse

    parser = argparse.ArgumentParser(description="Retrieve Jira tickets via wtmcp")
    parser.add_argument("--jql", default="assignee = currentUser()", help="JQL query")
    parser.add_argument("--max-results", type=int, default=100)
    parser.add_argument("--fields", nargs="+", metavar="FIELD", help="Fields to include in results")
    parser.add_argument("--url", default=WTMCP_BASE_URL, help="wtmcp MCP endpoint URL")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if not connect(args.url):
        logger.error("Cannot proceed without wtmcp connection")
        return 1

    tickets = search_jira(args.jql, args.max_results, args.fields)

    if not tickets:
        logger.warning("No tickets found")
        return 1

    if args.json:
        print(json.dumps(tickets, indent=2))
    else:
        print_tickets(tickets)

    return 0


if __name__ == "__main__":
    sys.exit(main())
