#!/usr/bin/env python3
"""Generate a configuration-driven triage report in Markdown."""

# /// script
# dependencies = [
#     "requests>=2.31.0",
#     "PyYAML>=6.0",
# ]
# ///

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))

from config import ConfigError, load_config  # noqa: E402
from providers import collect  # noqa: E402
from report import render  # noqa: E402

logger = logging.getLogger(__name__)


def _report_sections(markdown: str) -> list[str]:
    """Split a report into the title and each bold group section."""
    lines = markdown.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("**") and line.endswith("**")]
    if not starts:
        return [markdown]
    sections = []
    if starts[0]:
        sections.append("\n".join(lines[:starts[0]]).rstrip() + "\n")
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        contents = "\n".join(lines[start:end]).strip()
        if contents:
            sections.append(contents + "\n")
    return sections


def publish_to_gdoc(markdown: str, config: dict, document_id: str | None = None) -> str | None:
    """Write each report section to a new or existing Google Doc."""
    import retrieve_jira_tickets as wtmcp

    mcp_url = config.get("mcp", {}).get("url")
    if not mcp_url or not wtmcp.connect(mcp_url):
        logger.error("Cannot connect to MCP service for Google Docs")
        return None
    if not document_id:
        document_title = f"Triage Document - {date.today().isoformat()}"
        result = wtmcp.call_tool("gdocs_create_document", {"title": document_title, "dry_run": False}, timeout=120)
        if not result:
            logger.error("Failed to create Google Doc")
            return None
        document_match = re.search(r"https://docs\.google\.com/document/d/([^/\s]+)", result)
        document_id = document_match.group(1) if document_match else result.strip()
    else:
        document_match = re.search(r"https://docs\.google\.com/document/d/([^/\s]+)", document_id)
        document_id = document_match.group(1) if document_match else document_id
    for section in _report_sections(markdown):
        written = wtmcp.call_tool(
            "gdocs_write",
            {
                "document_id_or_url": document_id,
                "contents": section,
                "is_markdown": True,
                "append_to_end": True,
                "dry_run": False,
            },
            timeout=120,
        )
        if written is None:
            logger.error("Failed to write triage report to Google Doc")
            return None
    return f"https://docs.google.com/document/d/{document_id}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a configured triage report")
    parser.add_argument("-c", "--config", help="YAML configuration path")
    parser.add_argument(
        "--gdoc", nargs="?", const="", metavar="DOCUMENT_ID",
        help="Write the report to a new document, or append to DOCUMENT_ID",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        logger.error("Invalid configuration: %s", exc)
        return 2
    logger.info("Collecting configured triage sources")
    items = collect(config)
    logger.info("Rendering %d normalized items", len(items))
    markdown = render(items, config)
    if args.gdoc is not None:
        document_url = publish_to_gdoc(markdown, config, args.gdoc or None)
        if not document_url:
            return 1
        logger.info("Google Doc created: %s", document_url)
        print(document_url)
    else:
        print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
