#!/usr/bin/env python3
"""IDM triage: list new tickets and fetch their comments."""

# /// script
# dependencies = [
#     "requests>=2.31.0",
# ]
# ///

import csv
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import retrieve_jira_tickets as jira

logger = logging.getLogger(__name__)

TRIAGE_JQL = 'AssignedTeam[Dropbox] = rhel-idm-ipa AND status = "New" AND createdDate >=-2w AND issuetype in (Bug, Story, Vulnerability)'


def _parse_comment_text(cb: str, user_name=None) -> str:
    """Extract all text and mention fragments from a comment block."""
    tokens = []
    mailto_emails = re.findall(r"mailto:([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@redhat\.com)\b", cb, re.IGNORECASE)
    # Scan each line for text nodes and mention nodes in document order.
    for line in cb.splitlines():
        # Plain text node: - text: "value"  or  text: "value"
        m = re.match(r'\s*-?\s*text:\s+"?(.+?)"?\s*$', line)
        if m:
            tokens.append(m.group(1))
            continue
        # Mention node: text: @Display Name  (inside attrs block)
        m = re.match(r'\s*text:\s+(@\S.*)', line)
        if m:
            tokens.append(m.group(1).lstrip("@"))
    text = " ".join(tokens)

    def mention(email: str) -> str:
        if user_name:
            cached_user = user_name(email)
            if cached_user and cached_user.name and cached_user.name != email:
                return f"@{cached_user.name}"
        return email

    for email in mailto_emails:
        text = re.sub(re.escape(email), lambda _: mention(email), text, flags=re.IGNORECASE)
    # Jira sometimes emits email addresses as ordinary text rather than links.
    # Do not prefix addresses that are already part of an @ mention.
    email_pattern = r"(?<![@\w(])([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@redhat\.com)\b"
    return re.sub(email_pattern, lambda match: mention(match.group(1)), text, flags=re.IGNORECASE)


def _parse_issues_response(text: str, user_name=None) -> dict[str, dict]:
    """Parse jira_get_issues response into {issue_key: {comments, extra_fields}}."""
    inner = re.sub(r"</?tool-result[^>]*>", "", text).strip()
    result = {}

    # jira_get_issues may return requested scalar fields as a compact table.
    table = re.search(r"issues\[\d+\]\{([^}]+)\}:\s*\n?\s*(.+)", inner, re.DOTALL)
    if table:
        columns = [column.strip().strip('"') for column in next(csv.reader([table.group(1)]))]
        rows = csv.reader(line for line in table.group(2).splitlines() if line.strip())
        for row in rows:
            values = dict(zip(columns, row))
            key = values.get("key", "")
            if key:
                result[key] = {
                    CUSTOM_FIELDS[field]: value
                    for field, value in values.items()
                    if field in CUSTOM_FIELDS and value not in ("", "null")
                }

    issue_blocks = re.findall(r"  - .+?(?=\n  - |\Z)", inner, re.DOTALL)
    for block in issue_blocks:
        key_match = re.search(r"    key:\s+(\S+)", block)
        if not key_match:
            continue
        key = key_match.group(1)
        issue_data: dict = {}

        # Extract top-level custom fields (quoted or unquoted names), skip nulls
        for m in re.finditer(r'^    "?([^":]+)"?:\s+(.+)$', block, re.MULTILINE):
            raw_field, value = m.group(1).strip(), m.group(2).strip()
            if raw_field not in CUSTOM_FIELDS or value == "null":
                continue
            issue_data[CUSTOM_FIELDS[raw_field]] = value.strip('"')

        # Extract comments
        comments = []
        comment_blocks = re.findall(
            r"        - author:.+?(?=\n        - author:|\n      maxResults:|\Z)",
            block,
            re.DOTALL,
        )
        for cb in comment_blocks:
            author_match = re.search(r"displayName:\s+(.+)", cb)
            email_match = re.search(r"emailAddress:\s+(\S+)", cb)
            created_match = re.search(r"\n          created:\s+\"([^\"]+)\"", cb)
            comments.append({
                "author": author_match.group(1).strip() if author_match else "",
                "email": email_match.group(1).strip() if email_match else "",
                "created": created_match.group(1) if created_match else "",
                "text": _parse_comment_text(cb, user_name),
            })
            if user_name and comments[-1]["email"]:
                cached_user = user_name(comments[-1]["email"])
                comments[-1]["display_name"] = cached_user.name
                comments[-1]["mention"] = cached_user.mention
        issue_data["comments"] = comments

        # Extract affects versions: versions[N]{...,name,...}: row
        versions_match = re.search(
            r"    versions\[\d+\]\{([^}]+)\}:\s*\n((?:      .+\n?)+)", block
        )
        affects_versions = []
        if versions_match:
            cols = [c.strip() for c in versions_match.group(1).split(",")]
            if "name" in cols:
                name_idx = cols.index("name")
                for row in versions_match.group(2).strip().splitlines():
                    parts = next(csv.reader([row.strip()]))
                    if len(parts) > name_idx:
                        affects_versions.append(parts[name_idx])
        issue_data["affects_versions"] = affects_versions or None

        result[key] = issue_data

    return result


CUSTOM_FIELDS = {
    "customfield_10667": "cve_id",
    "customfield_10859": "cvss_score",
}


def fetch_issue_details(
    issue_keys: list[str],
    user_name=None,
    include_comments: bool = True,
    include_versions: bool = True,
) -> dict[str, dict]:
    """Fetch comments and extra fields for all issues in a single call."""
    requested = []
    if include_comments:
        requested.append("comment")
    if include_versions:
        requested.append("versions")
    requested.extend(CUSTOM_FIELDS)
    fields = ",".join(requested)
    text = jira.call_tool(
        "jira_get_issues",
        {"issue_keys": ",".join(issue_keys), "fields": fields, "brief": False},
    )
    if not text:
        logger.error("Failed to fetch issue details")
        return {}
    return _parse_issues_response(text, user_name)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="IDM triage: new tickets")
    parser.add_argument("--jql", default=TRIAGE_JQL, help="Override JQL query")
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

    tickets = jira.search_jira(args.jql)
    if not tickets:
        logger.warning("No tickets found")
        return 0

    keys = [t["key"] for t in tickets if t.get("key")]
    logger.info("Fetching details for %d issues", len(keys))
    details_by_key = fetch_issue_details(keys)

    results = []
    for ticket in tickets:
        key = ticket.get("key", "")
        if not key:
            continue
        issue = dict(ticket)
        issue["url"] = f"https://redhat.atlassian.net/browse/{key}"
        issue.update(details_by_key.get(key, {}))
        results.append(issue)

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
