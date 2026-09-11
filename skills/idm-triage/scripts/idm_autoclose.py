#!/usr/bin/env python3
"""IPA auto-close: list tickets approaching auto-close."""

# /// script
# dependencies = [
#     "requests>=2.31.0",
# ]
# ///

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import retrieve_jira_tickets as jira

logger = logging.getLogger(__name__)

AUTOCLOSE_JQL = (
    'assignedteam = rhel-idm-ipa '
    'AND labels = auto-close-warning '
    'AND "Stale Date[Date]" < 30days'
)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="IPA auto-close warning tickets")
    parser.add_argument("--jql", default=AUTOCLOSE_JQL, help="Override JQL query")
    parser.add_argument("--url", default=jira.WTMCP_BASE_URL, help="wtmcp MCP endpoint URL")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    if not jira.connect(args.url):
        logger.error("Cannot connect to wtmcp")
        return 1

    tickets = jira.search_jira(
        args.jql,
        fields=["key", "summary", "status", "assignee", "priority", "customfield_10812"],
    )
    if not tickets:
        logger.warning("No tickets found")
        return 0

    results = []
    for t in tickets:
        if not t.get("key"):
            continue
        issue = dict(t)
        issue["url"] = f"https://redhat.atlassian.net/browse/{t['key']}"
        # Rename raw custom field to friendly name
        if "customfield_10812" in issue:
            issue["stale_date"] = issue.pop("customfield_10812")
        results.append(issue)

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
