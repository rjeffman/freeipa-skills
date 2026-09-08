"""Markdown report rendering for normalized triage items."""

from collections import defaultdict
import re

from models import Item


def _plain_user_mentions(text: str) -> str:
    """Remove known automation markers without altering user mentions or emails."""
    return re.sub(r"@SFDC SYSTEM USER\b", "SFDC SYSTEM USER", text)


def _mention(value: str) -> str:
    if re.fullmatch(r"[^\s@]+@[^\s@]+", value):
        return value
    return f"@{value}"


def _report_text(text: str) -> str:
    return text.replace("\t", "    ")


def _field_visible(item: Item, field: str, group: str | None = None, default: bool = True) -> bool:
    policy = item.metadata.get("_display_fields_by_group", {}).get(
        group, item.metadata.get("_display_fields", {})
    )
    if field in policy.get("hide", []):
        return False
    show = policy.get("show")
    return default if show is None else field in show


def _item_line(item: Item, indent: str = "-", skip_commenters: set[str] | None = None, group: str | None = None) -> list[str]:
    context = f" ({item.component}/{item.repository})" if item.repository else ""
    lines = [f"{indent} [{item.identifier}]({item.url}) - {_report_text(item.summary)}{context}"]
    nested = f"    {indent}"
    if group == "autoclose" and item.metadata.get("stale_date"):
        lines.append(f"{nested} Closes at: {item.metadata['stale_date']}")
    author = _plain_user_mentions(item.author)
    if item.provider == "jira" and author.casefold() in (skip_commenters or set()):
        author = ""
    if author and _field_visible(item, "author", group):
        if item.metadata.get("author_mention"):
            author = _mention(author)
        lines.append(f"{nested} **Author:** {author}")
    field_labels = {
        "component": "Component", "product": "Product", "status": "Status",
        "priority": "Priority", "severity": "Severity", "assignee": "Assignee",
        "duedate": "Due Date", "sladate": "SLA Date",
        "cve_id": "CVE ID", "cvss_score": "CVSS Score", "keywords": "Keywords",
    }
    for field, label in field_labels.items():
        value = item.metadata.get(field)
        if value not in (None, "") and _field_visible(item, field, group):
            lines.append(f"{nested} **{label}:** {_report_text(str(value))}")
    policy = item.metadata.get("_display_fields_by_group", {}).get(
        group, item.metadata.get("_display_fields", {})
    )
    known_fields = set(field_labels) | {"author", "comments", "labels", "summary", "url"}
    for field in policy.get("show", []) or []:
        if field in known_fields:
            continue
        value = item.metadata.get(field)
        if value not in (None, "") and _field_visible(item, field, group):
            label = field.replace("_", " ").title()
            lines.append(f"{nested} **{label}:** {_report_text(str(value))}")
    labels = item.labels
    matched_labels = {label.casefold() for label in item.matched_labels}
    labels = [label for label in labels if label.casefold() not in matched_labels]
    if "test_failures" in item.groups:
        labels = [label for label in labels if label.casefold() != "test-failure"]
    if labels and _field_visible(item, "labels", group):
        lines.append(f"{nested} **Labels:** {', '.join(labels)}")
    for comment in item.comments if _field_visible(item, "comments", group) and isinstance(item.comments, list) else []:
        if not isinstance(comment, dict):
            continue
        commenter = (comment.get("display_name") or comment.get("author", "")).casefold()
        if item.provider == "jira" and commenter in (skip_commenters or set()):
            continue
        text = _report_text(_plain_user_mentions(comment.get("text", "")))
        if text:
            author = _plain_user_mentions(comment.get("display_name") or comment.get("author") or comment.get("email", ""))
            if item.provider == "jira" and comment.get("mention", True):
                author = _mention(author)
            lines.append(f"{nested} [{author}] {text}")
    return lines


def _provider_title(provider: str) -> str:
    return {"jira": "Jira", "bugzilla": "Bugzilla", "github": "GitHub", "codeberg": "Codeberg", "forgejo": "Forgejo"}.get(provider, provider.title())


def _group_title(group: str, titles: dict[str, str]) -> str:
    return titles.get(group, group.replace("_", " ").title())


def _source_lines(group_items: list[Item], group: str, skip_commenters: set[str]) -> list[str]:
    lines = []
    by_component = defaultdict(list)
    for item in group_items:
        by_component[item.component].append(item)
    for component, component_items in by_component.items():
        lines.append(f"- **{component}**")
        by_provider = defaultdict(list)
        for item in component_items:
            by_provider[item.provider].append(item)
        provider_order = ["jira"] + [provider for provider in by_provider if provider != "jira"]
        for provider in provider_order:
            if provider not in by_provider:
                continue
            lines.append(f"    - **{_provider_title(provider)}**")
            for item in by_provider[provider]:
                lines.extend(_item_line(item, indent="        -", skip_commenters=skip_commenters, group=group))
    return lines


def render(items: list[Item], config: dict) -> str:
    groups = defaultdict(list)
    fallback = config["report"].get("fallback_group")
    skip_commenters = {str(name).casefold() for name in config["report"].get("skip_commenters", [])}
    for item in items:
        matched = item.groups or ([fallback] if fallback else [])
        for group in matched:
            groups[group].append(item)
    title = config["report"].get("title", "")
    lines = [f"## {title}"] if title else []
    for group in config["report"]["groups"]:
        group_items = groups.get(group, [])
        if not group_items:
            continue
        titles = config["report"].get("group_titles", {})
        lines.extend(["", f"**{_group_title(group, titles)}**", ""])
        if group in {"triage", "test_failures", "needs_review", "vulnerabilities"}:
            lines.extend(_source_lines(group_items, group, skip_commenters))
        else:
            for item in group_items:
                lines.extend(_item_line(item, skip_commenters=skip_commenters, group=group))
    if not lines:
        lines.extend(["", "No triage items found."])
    return "\n".join(lines) + "\n"
