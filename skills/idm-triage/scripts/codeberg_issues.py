#!/usr/bin/env python3
"""Retrieve Codeberg/Forgejo issues and pull requests as JSON."""

# /// script
# dependencies = [
#     "requests>=2.31.0",
# ]
# ///

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)


def fetch_items(base_url: str, repository: str, state: str = "open", days: int = 7) -> list[dict]:
    """Fetch recently created issues and their comments from a Forgejo API."""
    api_base = f"{base_url.rstrip('/')}/api/v1/repos/{repository}"
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {"Accept": "application/json"}
    token = os.environ.get("CODEBERG_TOKEN", "")
    if token:
        headers["Authorization"] = f"token {token}"

    result = []
    page = 1
    limit = 50
    while True:
        response = requests.get(
            f"{api_base}/issues",
            params={"type": "issues", "state": state, "since": since, "limit": limit, "page": page},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        for issue in batch:
            if issue.get("created_at", "") < since:
                continue
            number = issue.get("number")
            comments_response = requests.get(
                f"{api_base}/issues/{number}/comments",
                headers=headers,
                timeout=30,
            )
            comments_response.raise_for_status()
            logger.info("Fetched comments repository=%s issue=%s", repository, number)
            result.append(
                {
                    "number": number,
                    "url": issue.get("html_url", ""),
                    "summary": issue.get("title", ""),
                    "reporter": (issue.get("user") or {}).get("login", ""),
                    "labels": [label.get("name", "") for label in issue.get("labels", [])],
                    "created_at": issue.get("created_at", ""),
                    "comments": [
                        {
                            "author": (comment.get("user") or {}).get("login", ""),
                            "created_at": comment.get("created_at", ""),
                            "text": comment.get("body", ""),
                        }
                        for comment in comments_response.json()
                    ],
                }
            )
        if len(batch) < limit:
            break
        page += 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Query Codeberg/Forgejo issues")
    parser.add_argument("repository", help="repository as owner/name")
    parser.add_argument("state", nargs="?", default="open", choices=["open", "closed", "all"])
    parser.add_argument("--url", default=os.environ.get("CODEBERG_URL", "https://codeberg.org"))
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )
    try:
        print(json.dumps(fetch_items(args.url, args.repository, args.state, args.days), indent=2))
    except requests.RequestException as exc:
        logger.error("Codeberg API error: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
