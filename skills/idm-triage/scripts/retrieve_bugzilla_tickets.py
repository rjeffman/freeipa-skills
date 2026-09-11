#!/usr/bin/env python3
"""Retrieve Bugzilla bugs and comments through the wtmcp MCP server."""

from __future__ import annotations

import ast
import csv
import json
import logging
import re
from typing import Optional

import retrieve_jira_tickets as mcp
import yaml

logger = logging.getLogger(__name__)


def _value(value: str):
    value = value.strip().strip('"')
    if value in {"", "null", "None"}:
        return ""
    if value.startswith(("[", "{")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(value)
            except (ValueError, SyntaxError):
                pass
    return value


def _parse_records(text: str, section: str) -> list[dict]:
    """Parse wtmcp's table output, with JSON as a useful test/fallback format."""
    inner = re.sub(r"</?tool-result[^>]*>", "", text).strip()
    try:
        data = yaml.safe_load(inner)
        if isinstance(data, dict):
            values = next(
                (value for key, value in data.items() if key == section or str(key).startswith(f"{section}[")),
                None,
            )
            if isinstance(values, list):
                records = []
                for record in values:
                    if not isinstance(record, dict):
                        continue
                    normalized = {}
                    for key, value in record.items():
                        normalized[re.sub(r"\[\d+\]$", "", str(key))] = value
                    records.append(normalized)
                if records:
                    return records
    except yaml.YAMLError:
        pass
    try:
        data = json.loads(inner)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            values = data.get(section) or data.get("bugs") or data.get("comments")
            if isinstance(values, list):
                return [item for item in values if isinstance(item, dict)]
    except json.JSONDecodeError:
        pass

    match = re.search(rf"{re.escape(section)}\[\d+\]\{{([^}}]+)\}}:\s*(.*)", inner, re.DOTALL)
    if not match:
        return []
    columns = [column.strip() for column in next(csv.reader([match.group(1)]))]
    rows = []
    for line in match.group(2).splitlines():
        line = line.strip()
        if not line or re.match(r"\w+:\s", line):
            continue
        values = next(csv.reader([line], skipinitialspace=True))
        if values:
            rows.append({key: _value(value) for key, value in zip(columns, values)})
    return rows


def connect(base_url: str) -> bool:
    """Initialize the shared wtmcp session."""
    return mcp.connect(base_url)


def call_tool(tool_name: str, arguments: dict, timeout: int = 30) -> Optional[str]:
    return mcp.call_tool(tool_name, arguments, timeout=timeout)


def search_bugzilla(
    product: str | None = None,
    component: str | None = None,
    assigned_to: str | None = None,
    status: str | None = None,
    query: str | None = None,
    max_results: int = 200,
) -> list[dict]:
    arguments = {"max_results": max_results, "brief": False}
    if product:
        arguments["product"] = product
    if component:
        arguments["component"] = component
    if assigned_to:
        arguments["assigned_to"] = assigned_to
    if status:
        arguments["status"] = status
    if query:
        arguments["query"] = query
    fields = "id,summary,status,assigned_to,reporter,keywords,product,component,creation_time,url"
    arguments["include_fields"] = fields
    text = call_tool("bugzilla_search", arguments)
    if not text:
        return []
    bugs = _parse_records(text, "bugs")
    logger.info("Retrieved %d Bugzilla bugs", len(bugs))
    return bugs


def get_bugs(bug_ids: list[str]) -> list[dict]:
    if not bug_ids:
        return []
    text = call_tool(
        "bugzilla_get_bugs",
        {
            "bug_ids": ",".join(bug_ids),
            "brief": False,
            "include_fields": "id,summary,status,assigned_to,reporter,keywords,product,component,creation_time,url",
        },
    )
    return _parse_records(text, "bugs") if text else []


def get_comments(bug_id: str) -> list[dict]:
    text = call_tool("bugzilla_get_comments", {"bug_id": bug_id})
    return _parse_records(text, "comments") if text else []
