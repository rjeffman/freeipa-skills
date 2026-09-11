#!/usr/bin/env python3
"""Retrieve open GitHub issues/PRs, optionally filtered by labels and/or creation date, as JSON."""

# /// script
# dependencies = [
#     "requests>=2.31.0",
# ]
# ///

import json
import logging
import os
import sys
import requests

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def github_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"Accept": "application/vnd.github.v3+json"})
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    return session


def fetch_issues(
    session: requests.Session,
    repo: str,
    labels: list[str] | None = None,
    since: str | None = None,
    max_pages: int = 100,
    state: str = "open",
) -> list[dict]:
    """Fetch all open issues/PRs, optionally filtered by labels and/or creation date (paginated)."""
    from datetime import datetime, timezone

    url = f"{GITHUB_API}/repos/{repo}/issues"
    params: dict = {"state": state, "per_page": 100, "page": 1}
    if labels:
        params["labels"] = ",".join(labels)
    if since:
        params["since"] = since
    results = []

    while True:
        logger.info("GitHub request repository=%s page=%d", repo, params["page"])
        response = session.get(url, params=params, timeout=30)
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        # Filter by created_at when since is set (API filters by updated_at)
        if since:
            batch = [i for i in batch if i["created_at"] >= since]
        results.extend(batch)
        if len(batch) < params["per_page"]:
            break
        if params["page"] >= max_pages:
            logger.warning("Stopping GitHub pagination at max_pages=%d", max_pages)
            break
        params["page"] += 1

    return results


def fetch_comments(session: requests.Session, repo: str, number: int) -> list[dict]:
    url = f"{GITHUB_API}/repos/{repo}/issues/{number}/comments"
    response = session.get(url, params={"per_page": 100}, timeout=30)
    response.raise_for_status()
    return [
        {"author": c["user"]["login"], "created_at": c["created_at"], "text": c["body"]}
        for c in response.json()
    ]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Query GitHub issues/PRs by label")
    parser.add_argument("repo", help="GitHub repository as owner/repo")
    parser.add_argument("labels", nargs="*", help="Labels to filter by (optional)")
    parser.add_argument("--days", type=int, default=None, help="Only include issues created in the last N days")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--comments", action="store_true", help="Fetch comments for each item")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s - %(levelname)s - %(message)s", stream=sys.stderr)

    since = None
    if args.days:
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    session = github_session()

    logger.info("Fetching issues from %s labels=%s days=%s", args.repo, args.labels or "any", args.days)
    try:
        items = fetch_issues(session, args.repo, args.labels or None, since)
    except requests.HTTPError as e:
        logger.error("GitHub API error: %s", e)
        return 1

    results = []
    for item in items:
        number = item["number"]
        comments = []
        if args.comments:
            logger.info("Fetching comments for #%d", number)
            comments = fetch_comments(session, args.repo, number)
        value = {
            "number": number,
            "url": item["html_url"],
            "summary": item["title"],
            "reporter": item["user"]["login"],
            "labels": [lbl["name"] for lbl in item["labels"]],
            "created_at": item["created_at"],
        }
        value.update({"comments": comments} if comments else {})
        results.append(value)

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
