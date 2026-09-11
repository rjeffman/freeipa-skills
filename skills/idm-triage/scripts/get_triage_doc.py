#!/usr/bin/env python3
"""Generate the daily IDM triage report and upload it to a new Google Doc."""

# /// script
# dependencies = [
#     "requests>=2.31.0",
# ]
# ///

import logging
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import retrieve_jira_tickets as wtmcp

logger = logging.getLogger(__name__)
SCRIPTS = Path(__file__).parent


def run_triage(config: str | None = None) -> str:
    """Run idm_triage.py and return its markdown output."""
    logger.info("Running idm_triage.py...")
    command = ["uv", "run", str(SCRIPTS / "idm_triage.py")]
    if config:
        command.extend(["--config", config])
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("idm_triage.py failed:\n%s", result.stderr)
        sys.exit(1)
    return result.stdout


def create_doc(title: str) -> str | None:
    """Create a new Google Doc and return its document ID."""
    result = wtmcp.call_tool("gdocs_create_document", {"title": title, "dry_run": False}, timeout=120)
    if not result:
        logger.error("Failed to create Google Doc")
        return None
    logger.debug("gdocs_create_document response: %s", result[:200])
    # Response contains the document URL or ID — extract it
    import re
    m = re.search(r"https://docs\.google\.com/document/d/([^/\s]+)", result)
    if m:
        return m.group(1)
    # Fallback: return raw result as ID
    return result.strip()


def write_doc(doc_id: str, file_path: str) -> bool:
    """Write markdown content from file_path into the Google Doc."""
    result = wtmcp.call_tool(
        "gdocs_write",
        {
            "document_id_or_url": doc_id,
            "file_path": file_path,
            "is_markdown": True,
            "append_to_end": True,
            "dry_run": False,
        },
        timeout=120,
    )
    if result is None:
        logger.error("Failed to write to Google Doc")
        return False
    return True


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Generate IDM triage report as a Google Doc")
    parser.add_argument("--url", default=wtmcp.WTMCP_BASE_URL, help="wtmcp MCP endpoint URL")
    parser.add_argument("--config", help="triage YAML configuration path")
    parser.add_argument("--print", action="store_true", help="print Markdown without publishing")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    markdown = run_triage(args.config)
    if args.print:
        print(markdown, end="")
        return 0
    if not wtmcp.connect(args.url):
        logger.error("Cannot connect to wtmcp")
        return 1

    title = "@today"
    if args.config:
        sys.path.insert(0, str(SCRIPTS))
        from config import load_config
        title = load_config(args.config)["report"]["title"]

    logger.info("Creating Google Doc: %s", title)
    doc_id = create_doc(title)
    if not doc_id:
        return 1
    logger.info("Created document: https://docs.google.com/document/d/%s", doc_id)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(markdown)
        tmp_path = f.name

    logger.info("Uploading report to Google Doc...")
    if not write_doc(doc_id, tmp_path):
        return 1

    Path(tmp_path).unlink(missing_ok=True)

    logger.info("Done: https://docs.google.com/document/d/%s", doc_id)
    print(f"https://docs.google.com/document/d/{doc_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
