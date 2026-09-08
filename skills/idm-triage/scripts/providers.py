"""Collection adapters for the supported triage providers."""

from __future__ import annotations

import os
import sys
import logging
import re
import time
from datetime import datetime
from pathlib import Path
import idm_triage_issues as issue_module
from user_cache import UserCache, jira_display_name

from models import Item

logger = logging.getLogger(__name__)
SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))


def _stale_date(raw: dict, configured_field: str) -> str:
    """Return Jira's Stale Date custom field in the report's date format."""
    value = next(
        (raw.get(field) for field in ("stale_date", configured_field, "Stale Date", "Stale Date[Date]", "customfield_10812") if raw.get(field)),
        None,
    )
    if not value:
        return "unknown"
    text = str(value).strip().strip('"')
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10] if len(text) >= 10 and text[4] == "-" and text[7] == "-" else text


def _identity(value):
    if not isinstance(value, dict):
        return value or ""
    return value.get("name") or value.get("login") or value.get("username") or value.get("email") or ""


def _date_only(value):
    if not value:
        return value
    text = str(value).strip().strip('"')
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _item(
    component: dict,
    source: dict,
    raw: dict,
    item_type: str,
    groups: list[str] | None = None,
    display_fields: dict | None = None,
) -> Item:
    provider = source["provider"]
    identifier = str(raw.get("key") or raw.get("number") or "")
    labels = raw.get("labels", [])
    if not isinstance(labels, list):
        labels = [str(labels)] if labels else []
    if labels and isinstance(labels[0], dict):
        labels = [label.get("name", "") for label in labels]
    comments = raw.get("comments", [])
    if not isinstance(comments, list):
        comments = []
    author = _identity(raw.get("reporter") or raw.get("author") or raw.get("user"))
    assignee = _identity(raw.get("assignee") or raw.get("assigned_to"))
    item = Item(
            component=component["name"], provider=provider,
            repository=source.get("repository", ""), roles=source.get("roles", []),
            item_type=item_type, identifier=identifier,
            url=raw.get("url") or raw.get("html_url", ""),
            summary=raw.get("summary") or raw.get("title", ""),
            author=author,
            labels=labels, comments=comments,
            groups=list(groups or []),
            metadata={key: value for key, value in raw.items() if key not in {"key", "number", "url", "html_url", "summary", "title", "reporter", "author", "user", "labels", "comments"}},
    )
    if "status" not in item.metadata and raw.get("state"):
        item.metadata["status"] = raw["state"]
    if "duedate" not in item.metadata:
        item.metadata["duedate"] = raw.get("due_date") or raw.get("duedate")
    if "sladate" not in item.metadata:
        item.metadata["sladate"] = raw.get("sla_date") or raw.get("sladate")
    item.metadata["duedate"] = _date_only(item.metadata.get("duedate"))
    item.metadata["sladate"] = _date_only(item.metadata.get("sladate"))
    if raw.get("assignee") or raw.get("assigned_to"):
        item.metadata["assignee"] = assignee
    elif "assignee" not in item.metadata:
        item.metadata["assignee"] = assignee
    if display_fields or source.get("_display_fields"):
        item.metadata["_display_fields"] = display_fields or source["_display_fields"]
    group_policies = {
        group["name"]: group.get("display_fields", source.get("_display_fields", {}))
        for group in source.get("groups", [])
        if isinstance(group, dict) and group.get("name")
    }
    if group_policies:
        item.metadata["_display_fields_by_group"] = group_policies
    item.matched_labels = list(dict.fromkeys(
        actual
        for group in source.get("groups", [])
        if (group.get("name") or next(iter(group))) in item.groups
        for configured in group.get("filter", group).get("labels", [])
        for actual in labels
        if str(configured).casefold() == str(actual).casefold()
    ))
    if provider == "jira" and raw.get("cve_id") and "autoclose" not in item.groups:
        item.groups = ["vulnerabilities"]
    return item


def _groups_for(raw: dict, item_type: str, source: dict) -> list[str]:
    from datetime import datetime, timedelta, timezone

    result = []
    raw_labels = raw.get("labels", [])
    labels = {
        str(label.get("name", "") if isinstance(label, dict) else label).casefold()
        for label in raw_labels
    }
    for group in source.get("groups", []):
        filters = group.get("filter", group)
        group_name = group.get("name") or next(iter(group))
        expected_type = {"issue": "issue", "pr": "pull_request"}.get(filters.get("is"))
        if expected_type and item_type != expected_type:
            continue
        if filters.get("state") and raw.get("state") != filters["state"]:
            continue
        if filters.get("lookback_days") and raw.get("created_at"):
            try:
                created = datetime.fromisoformat(raw["created_at"].replace("Z", "+00:00"))
                cutoff = datetime.now(timezone.utc) - timedelta(days=filters["lookback_days"])
                if created < cutoff:
                    continue
            except (TypeError, ValueError):
                pass
        configured_labels = {str(label).casefold() for label in filters.get("labels", [])}
        if configured_labels and not configured_labels.intersection(labels):
            continue
        excluded_labels = {str(label).casefold() for label in filters.get("exclude_labels", [])}
        if excluded_labels.intersection(labels):
            continue
        result.append(group_name)
    return result


def _resolve_cached_author(item: Item, cache: UserCache) -> Item:
    if not item.author:
        return _resolve_cached_identity(item, "assignee", cache)
    identifier = item.author
    was_cached = identifier in cache.users.get(item.provider, {})
    cached = cache.resolve(item.provider, identifier, should_lookup=False)
    item.author = cached.name
    if was_cached:
        item.metadata["author_mention"] = cached.mention
    return _resolve_cached_identity(item, "assignee", cache)


def _resolve_cached_identity(item: Item, field: str, cache: UserCache) -> Item:
    identifier = item.metadata.get(field)
    if not identifier or not isinstance(identifier, str):
        return item
    cached = cache.resolve(item.provider, identifier, should_lookup=False)
    item.metadata[field] = cached.name
    return item


def _bugzilla_values(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    if not value:
        return []
    text = str(value).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [item.strip().strip("'\"") for item in text.split(",") if item.strip()]


def _bugzilla_date(value: str) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().strip('"').replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _github_item_url(repository: str, number: int, item_type: str, web_base: str = "https://github.com") -> str:
    path_type = "pull" if item_type == "pull_request" else "issues"
    return f"{web_base.rstrip('/')}/{repository}/{path_type}/{number}"


def _ensure_mcp_session(config: dict) -> bool:
    """Connect to the MCP server once per process; reuse the session."""
    import retrieve_jira_tickets as jira
    if jira._session_id:
        return True
    mcp_url = config.get("mcp", {}).get("url", jira.WTMCP_BASE_URL)
    return jira.connect(mcp_url)


def _jira_field_request(group: dict, configured_fields: dict) -> tuple[dict[str, str], bool, bool]:
    """Return Jira field mappings and detail sections required by a group."""
    policy = group.get("display_fields", {})
    show = policy.get("show")
    hide = set(policy.get("hide", []))
    requested = set(configured_fields)
    if show is None:
        requested.update({"comments", "versions"})
    else:
        requested.update(show)
    requested.difference_update(hide)
    filters = group.get("filter", {})
    requested.update(
        field for field in filters
        if field in {"status", "assignee", "priority", "labels", "duedate", "sladate"}
    )
    if "lookahead_days" in filters:
        requested.add("stale_date")

    aliases = {"sla_date": "sladate", "due_date": "duedate"}
    mapping = {
        actual: aliases.get(logical, logical)
        for logical, actual in configured_fields.items()
    }
    standard_fields = {"status", "assignee", "priority", "labels", "duedate", "sladate"}
    for logical in requested.intersection(standard_fields):
        mapping.setdefault(logical, logical)
    include_comments = "comments" in requested
    include_versions = "versions" in requested
    return mapping, include_comments, include_versions


def collect_jira(component: dict, source: dict, config: dict) -> list[Item]:
    import retrieve_jira_tickets as jira
    jira_config = config["providers"]["jira"]
    configured_fields = jira_config.get("custom_fields", {})
    logger.info("Starting Jira source component=%s", component["name"])
    if not _ensure_mcp_session(config):
        return []
    user_cache = UserCache(lambda email: jira_display_name(jira.call_tool, email))
    stale_field = jira_config.get("custom_fields", {}).get("stale_date", "customfield_10812")
    search_fields = ["key", "summary", "reporter"]
    result = []
    # fetch_issue_details uses jira_get_issues (different MCP tool from jira_search)
    # which prevents the MCP server from replaying a cached jira_search response
    # on the next call.
    def resolve_jira_user(email: str):
        local_part = email.partition("@")[0]
        return user_cache.resolve("jira", email, "-" in local_part or "+" in local_part)

    for group in source["groups"]:
        is_autoclose = "lookahead_days" in group["filter"]
        field_mapping, include_comments, include_versions = _jira_field_request(group, configured_fields)
        issue_module.CUSTOM_FIELDS = field_mapping
        fields = search_fields + list(field_mapping)
        tickets = jira.search_jira(group["query"], fields=fields)
        details = issue_module.fetch_issue_details(
            [ticket["key"] for ticket in tickets if ticket.get("key")], resolve_jira_user,
            include_comments=include_comments,
            include_versions=include_versions,
        ) if tickets and (field_mapping or include_comments or include_versions) else {}
        if is_autoclose and tickets:
            # Fetch the stale date separately; it is not returned by the search tool.
            stale_text = jira.call_tool(
                "jira_get_issues",
                {
                    "issue_keys": ",".join(ticket["key"] for ticket in tickets if ticket.get("key")),
                    "fields": stale_field,
                    "brief": False,
                },
            )
            if stale_text:
                for key, values in issue_module._parse_issues_response(stale_text).items():
                    details.setdefault(key, {}).update(values)
        for ticket in tickets:
            key = ticket.get("key")
            if not key:
                continue
            raw = dict(ticket)
            raw.update(details.get(key, {}))
            author_value = raw.get("reporter") or raw.get("author")
            if isinstance(author_value, str) and "@" in author_value:
                local_part = author_value.partition("@")[0]
                cached_author = user_cache.resolve("jira", author_value, "-" in local_part or "+" in local_part)
                raw["reporter"] = cached_author.name
            if is_autoclose:
                raw["stale_date"] = _stale_date(raw, stale_field)
            raw["url"] = f'{jira_config["url"].rstrip("/")}/browse/{key}'
            raw["labels"] = raw.get("labels", [])
            item = _item(component, source, raw, "issue", [group["name"]], group.get("display_fields"))
            _resolve_cached_author(item, user_cache)
            result.append(item)
    logger.info("Completed Jira source component=%s items=%d", component["name"], len(result))
    return result


def collect_bugzilla(component: dict, source: dict, config: dict) -> list[Item]:
    import retrieve_bugzilla_tickets as bugzilla
    from datetime import timedelta, timezone

    provider = config["providers"]["bugzilla"]
    if not _ensure_mcp_session(config):
        return []
    product = provider.get("product")
    source_components = source.get("components", [])
    assigned_to = source.get("assigned_to")
    keywords = {
        keyword.casefold()
        for key in ("keywords", "labels")
        for keyword in source.get(key, [])
    }
    excluded_keywords = {
        keyword.casefold()
        for key in ("exclude_keywords", "exclude_labels")
        for keyword in source.get(key, [])
    }
    result = []
    logger.info("Starting Bugzilla source component=%s product=%s", component["name"], product)

    for group in source["groups"]:
        filters = group["filter"]
        status = filters.get("status")
        group_keywords = {
            keyword.casefold()
            for key in ("keywords", "labels")
            for keyword in filters.get(key, [])
        }
        group_excluded_keywords = {
            keyword.casefold()
            for key in ("exclude_keywords", "exclude_labels")
            for keyword in filters.get(key, [])
        }
        raw_items = []
        search_components = source_components or [None]
        for bugzilla_component in search_components:
            raw_items.extend(
                bugzilla.search_bugzilla(
                    product=product,
                    component=bugzilla_component,
                    assigned_to=assigned_to,
                    status=status,
                    query=filters.get("query"),
                )
            )

        unique = {}
        for raw in raw_items:
            identifier = str(raw.get("id") or raw.get("bug_id") or raw.get("number") or "")
            if identifier:
                unique[identifier] = raw
        cutoff = None
        if filters.get("lookback_days"):
            cutoff = datetime.now(timezone.utc) - timedelta(days=filters["lookback_days"])
        for identifier, raw in unique.items():
            bug_keywords = _bugzilla_values(raw.get("keywords"))
            if keywords and not keywords.intersection(keyword.casefold() for keyword in bug_keywords):
                continue
            bug_keyword_set = {keyword.casefold() for keyword in bug_keywords}
            if excluded_keywords.intersection(bug_keyword_set):
                continue
            if group_keywords and not group_keywords.intersection(bug_keyword_set):
                continue
            if group_excluded_keywords.intersection(bug_keyword_set):
                continue
            created = _bugzilla_date(raw.get("creation_time") or raw.get("created_at"))
            if cutoff and created:
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created < cutoff:
                    continue
            raw = dict(raw)
            raw["number"] = identifier
            raw["labels"] = bug_keywords
            raw["reporter"] = raw.get("reporter") or raw.get("creator", "")
            raw["url"] = raw.get("url") or f'{provider["url"].rstrip("/")}/show_bug.cgi?id={identifier}'
            raw["comments"] = bugzilla.get_comments(identifier)
            result.append(_item(component, source, raw, "issue", [group["name"]], group.get("display_fields")))
    logger.info("Completed Bugzilla source component=%s items=%d", component["name"], len(result))
    return result


def collect_github(component: dict, source: dict, config: dict) -> list[Item]:
    import github_query_label as github
    import requests

    provider = config["providers"]["github"]
    base = provider["url"].rstrip("/")
    web_base = provider.get("web_url", "https://github.com").rstrip("/")
    session = github.github_session()
    session.headers["Accept"] = "application/vnd.github.v3+json"
    old_api = github.GITHUB_API
    github.GITHUB_API = base
    try:
        logger.info("Starting GitHub source component=%s repository=%s", component["name"], source["repository"])
        lookbacks = [group["filter"].get("lookback_days", 0) for group in source["groups"]]
        days = max(lookbacks) if any(lookbacks) else None
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ") if days else None
        states = {group["filter"].get("state", "open") for group in source["groups"]}
        state = states.pop() if len(states) == 1 else "all"
        raw_items = github.fetch_issues(
            session, source["repository"], None, since,
            max_pages=int(source.get("max_pages", 100)),
            state=state,
        )
    except requests.RequestException as exc:
        logger.warning("GitHub source failed repository=%s: %s", source["repository"], exc)
        return []
    finally:
        github.GITHUB_API = old_api
    result = []
    user_cache = UserCache(lambda identifier: None)
    for raw in raw_items:
        item_type = "pull_request" if "pull_request" in raw else "issue"
        groups = _groups_for(raw, item_type, source)
        if not groups:
            continue
        raw["url"] = _github_item_url(source["repository"], raw["number"], item_type, web_base)
        result.append(_resolve_cached_author(_item(component, source, raw, item_type, groups), user_cache))
    logger.info("Completed GitHub source repository=%s items=%d", source["repository"], len(result))
    return result


def collect_codeberg(component: dict, source: dict, config: dict) -> list[Item]:
    import requests
    from datetime import datetime, timedelta, timezone

    base = config["providers"][source["provider"]]["url"].rstrip("/")
    lookback_days = max(group["filter"].get("lookback_days", 7) for group in source["groups"])
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    states = {group["filter"].get("state", "open") for group in source["groups"]}
    params = {
        "type": "issues", "state": states.pop() if len(states) == 1 else "all",
        "since": since, "limit": 50, "page": 1,
    }
    token = os.environ.get("CODEBERG_TOKEN", "")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"token {token}"
    result = []
    user_cache = UserCache(lambda identifier: None)
    try:
        logger.info("Starting %s source component=%s repository=%s", source["provider"], component["name"], source["repository"])
        max_pages = int(source.get("max_pages", 100))
        while True:
            response = requests.get(f"{base}/api/v1/repos/{source['repository']}/issues", params=params, headers=headers, timeout=30)
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            logger.info("Fetched %s page=%d items=%d", source["provider"], params["page"], len(batch))
            for raw in batch:
                if raw.get("created_at", "") < since:
                    continue
                item_type = "pull_request" if raw.get("pull_request") else "issue"
                groups = _groups_for(raw, item_type, source)
                if not groups:
                    continue
                raw["url"] = raw.get("html_url", "")
                raw["reporter"] = (raw.get("user") or {}).get("login", "")
                raw["labels"] = [label.get("name", "") for label in raw.get("labels", [])]
                result.append(_resolve_cached_author(_item(component, source, raw, item_type, groups), user_cache))
            if len(batch) < params["limit"]:
                break
            if params["page"] >= max_pages:
                logger.warning("Stopping %s pagination at max_pages=%d", source["provider"], max_pages)
                break
            params["page"] += 1
    except requests.RequestException as exc:
        logger.warning("%s source failed repository=%s: %s", source["provider"], source["repository"], exc)
        return []
    logger.info("Completed %s source repository=%s items=%d", source["provider"], source["repository"], len(result))
    return result


def collect(config: dict) -> list[Item]:
    dispatch = {
        "jira": collect_jira, "bugzilla": collect_bugzilla,
        "github": collect_github, "codeberg": collect_codeberg, "forgejo": collect_codeberg,
    }
    items = []
    for component in config.get("components", []):
        for source in component.get("sources", []):
            if source.get("enabled", True):
                started = time.monotonic()
                try:
                    items.extend(dispatch[source["provider"]](component, source, config))
                except Exception:
                    logger.exception("Unexpected failure in %s source component=%s repository=%s", source["provider"], component["name"], source.get("repository", ""))
                logger.info("Finished source provider=%s component=%s elapsed=%.1fs", source["provider"], component["name"], time.monotonic() - started)
            else:
                logger.info("Skipping disabled source provider=%s component=%s", source.get("provider"), component["name"])
    return items
