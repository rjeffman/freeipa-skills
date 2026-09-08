"""Configuration loading, validation, and Jira query generation."""

from __future__ import annotations

from pathlib import Path
from string import Formatter
from typing import Any

import yaml


SUPPORTED_PROVIDERS = {"jira", "bugzilla", "github", "codeberg", "forgejo"}
SUPPORTED_CONNECTIONS = {"mcp", "github_api", "http"}
DEFAULT_CONFIG = Path(__file__).parent.parent / "assets" / "config.yaml"


class ConfigError(ValueError):
    """Raised when a triage configuration is invalid."""


def _mapping(value: Any, name: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a mapping")
    return value


def _list(value: Any, name: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be a list")
    return value


def _display_fields(value: Any, name: str) -> dict:
    fields = _mapping(value, name)
    result = {}
    for key in ("show", "hide"):
        if key not in fields:
            continue
        values = _list(fields[key], f"{name}.{key}")
        if not all(isinstance(field, str) and field for field in values):
            raise ConfigError(f"{name}.{key} must contain non-empty field names")
        aliases = {"sla_date": "sladate", "due_date": "duedate"}
        values = [aliases.get(field.casefold(), field.casefold()) for field in values]
        result[key] = list(dict.fromkeys(values))
    return result


def _quote(value: str) -> str:
    return value.replace('"', '\\"')


def _positive_int(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a positive integer") from exc
    if result <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return result


def _normalize_groups(
    source: dict,
    source_name: str,
    report_groups: set[str],
    allow_zero_days: bool = False,
) -> list[dict]:
    groups = _list(source.get("groups"), f"{source_name}.groups")
    if not groups:
        raise ConfigError(f"{source_name}.groups must not be empty")
    normalized = []
    for entry in groups:
        if not isinstance(entry, dict) or len(entry) != 1:
            raise ConfigError(f"{source_name}.groups entries must contain one group name")
        name, value = next(iter(entry.items()))
        if not isinstance(name, str) or not name:
            raise ConfigError(f"{source_name}.groups contains an invalid group name")
        if name not in report_groups:
            raise ConfigError(f"{source_name} references unknown report group: {name}")
        value = _mapping(value, f"{source_name}.groups.{name}")
        if source.get("provider") == "jira" and "filter" in value:
            raise ConfigError(f"{source_name}.groups.{name} must define Jira fields directly")
        filters = _mapping(value.get("filter", value), f"{source_name}.groups.{name}.filter")
        nested_fields = _mapping(value.get("fields"), f"{source_name}.groups.{name}.fields")
        direct_fields = {key: value[key] for key in ("show", "hide") if key in value}
        group_fields = _display_fields(
            {**nested_fields, **direct_fields},
            f"{source_name}.groups.{name}.fields",
        )
        if "lookback_days" in filters:
            if allow_zero_days and filters["lookback_days"] == 0:
                filters["lookback_days"] = 0
            else:
                filters["lookback_days"] = _positive_int(filters["lookback_days"], f"{source_name}.groups.{name}.lookback_days")
        if "lookahead_days" in filters:
            if allow_zero_days and filters["lookahead_days"] == 0:
                filters["lookahead_days"] = 0
            else:
                filters["lookahead_days"] = _positive_int(filters["lookahead_days"], f"{source_name}.groups.{name}.lookahead_days")
        for label_key in ("labels", "exclude_labels"):
            if label_key not in filters:
                continue
            labels = _list(filters[label_key], f"{source_name}.groups.{name}.{label_key}")
            if not all(isinstance(label, str) for label in labels):
                raise ConfigError(f"{source_name}.groups.{name}.{label_key} must contain strings")
        for keyword_key in ("keywords", "exclude_keywords"):
            if keyword_key not in filters:
                continue
            keywords = filters[keyword_key]
            if isinstance(keywords, str):
                keywords = [keywords]
                filters[keyword_key] = keywords
            keywords = _list(keywords, f"{source_name}.groups.{name}.{keyword_key}")
            if not all(isinstance(keyword, str) and keyword for keyword in keywords):
                raise ConfigError(f"{source_name}.groups.{name}.{keyword_key} must contain strings")
        if "is" in filters and filters["is"] not in {"issue", "pr"}:
            raise ConfigError(f"{source_name}.groups.{name}.is must be issue or pr")
        normalized.append({"name": name, "filter": filters, "display_fields": group_fields})
    return normalized


def _jira_query(provider: dict, filters: dict, projects: list[str] | None = None) -> str:
    team = provider["assigned_team"]
    issue_types = [str(item) for item in filters.get("issue_types", provider.get("issue_types", ["Bug", "Story", "Vulnerability"]))]
    status = str(filters.get("status", provider.get("status", "New")))
    lookback = filters.get("lookback_days")
    if lookback is None:
        lookback = provider.get("lookback", "2w")
    elif isinstance(lookback, int):
        lookback = f"{lookback}d"
    project_values = ", ".join('"' + _quote(str(project)) + '"' for project in (projects or []))
    if "lookahead_days" in filters:
        label = filters.get("label", "auto-close-warning")
        project_clause = f"project in ({project_values}) AND " if projects else ""
        return (
            f'{project_clause}assignedteam = {_quote(str(team))} '
            f'AND labels = {_quote(str(label))} AND "Stale Date[Date]" < {filters["lookahead_days"]}d'
        )
    project_clause = f"project in ({project_values}) AND " if projects else ""
    return (
        f'{project_clause}AssignedTeam[Dropbox] = "{_quote(str(team))}" AND status = "{_quote(status)}" '
        f'AND createdDate >= -{lookback} AND issuetype in ({", ".join(_quote(item) for item in issue_types)})'
    )


def jira_query(provider: dict, filters: dict, projects: list[str] | None = None) -> str:
    values = {
        "assigned_team": str(provider["assigned_team"]),
        "projects": ", ".join(f'"{_quote(str(project))}"' for project in (projects or [])),
        "lookback": str(filters.get("lookback_days", provider.get("lookback", "2w"))),
        "lookahead": str(filters.get("lookahead_days", provider.get("lookahead", "30d"))),
        "status": str(filters.get("status", provider.get("status", "New"))),
        "issue_types": ", ".join(str(item) for item in filters.get("issue_types", provider.get("issue_types", ["Bug", "Story", "Vulnerability"]))),
    }
    default = _jira_query(provider, filters, projects)
    template = filters.get("jql", default)
    fields = {field for _, field, _, _ in Formatter().parse(str(template)) if field}
    unknown = fields.difference(values)
    if unknown:
        raise ConfigError(f"unsupported Jira JQL placeholders: {', '.join(sorted(unknown))}")
    return str(template).format(**values)


def validate_config(config: dict) -> dict:
    if not isinstance(config, dict):
        raise ConfigError("configuration root must be a mapping")
    if any(key in config for key in ("jira",)):
        raise ConfigError("legacy top-level jira configuration is not supported; configure Jira under providers")

    mcp = _mapping(config.get("mcp"), "mcp")
    if mcp and mcp.get("transport") not in {None, "http"}:
        raise ConfigError("mcp.transport must be http")
    if mcp and not mcp.get("url"):
        raise ConfigError("mcp.url is required")

    providers = _mapping(config.get("providers"), "providers")
    for name, provider in providers.items():
        provider = _mapping(provider, f"providers.{name}")
        if provider.get("connection") not in SUPPORTED_CONNECTIONS:
            raise ConfigError(f"providers.{name}.connection must be one of: {', '.join(sorted(SUPPORTED_CONNECTIONS))}")
        if not provider.get("url"):
            raise ConfigError(f"providers.{name}.url is required")
        provider["fields"] = _display_fields(provider.get("fields"), f"providers.{name}.fields")
    jira = _mapping(providers.get("jira"), "providers.jira")
    if jira and not jira.get("assigned_team"):
        raise ConfigError("providers.jira.assigned_team is required")
    if jira and "projects" in jira and (
        not isinstance(jira["projects"], list)
        or not all(isinstance(project, str) and project for project in jira["projects"])
    ):
        raise ConfigError("providers.jira.projects must be a list of strings")
    if jira and not isinstance(jira.get("custom_fields", {}), dict):
        raise ConfigError("providers.jira.custom_fields must be a mapping")
    bugzilla = _mapping(providers.get("bugzilla"), "providers.bugzilla")
    if "bugzilla" in providers and "product" in bugzilla and not isinstance(bugzilla.get("product"), str):
        raise ConfigError("providers.bugzilla.product must be a string")

    report = _mapping(config.get("report"), "report")
    groups = report.get("groups")
    if not groups or not isinstance(groups, list) or not all(isinstance(group, str) for group in groups):
        raise ConfigError("report.groups must be a non-empty list of names")
    group_titles = report.setdefault("group_titles", {})
    if not isinstance(group_titles, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in group_titles.items()):
        raise ConfigError("report.group_titles must be a mapping of group names to titles")
    report_groups = set(groups)
    group_defaults = _mapping(report.get("group_defaults"), "report.group_defaults")
    for name, value in group_defaults.items():
        if name not in groups:
            raise ConfigError(f"report.group_defaults references unknown report group: {name}")
        value = _mapping(value, f"report.group_defaults.{name}")
        group_defaults[name] = _display_fields(value.get("fields"), f"report.group_defaults.{name}.fields")
    report["group_defaults"] = group_defaults
    global_groups = _mapping(config.get("groups"), "groups")
    normalized_global_groups = _normalize_groups(
        {"provider": "github", "groups": [{name: value} for name, value in global_groups.items()]},
        "groups",
        report_groups,
        allow_zero_days=True,
    ) if global_groups else []
    global_groups = {group["name"]: group for group in normalized_global_groups}

    sources_found = False
    for component in _list(config.get("components"), "components"):
        if not isinstance(component, dict) or not component.get("name"):
            raise ConfigError("each component requires a name")
        for source in _list(component.get("sources"), f"component {component.get('name')} sources"):
            sources_found = True
            if not isinstance(source, dict):
                raise ConfigError("sources must contain mappings")
            provider = source.get("provider")
            if provider not in SUPPORTED_PROVIDERS:
                raise ConfigError(f"unsupported provider: {provider}")
            if provider not in providers:
                raise ConfigError(f"source provider is not configured: {provider}")
            if provider not in {"jira", "bugzilla"} and not source.get("repository"):
                raise ConfigError(f"{provider} source requires repository")
            if provider == "bugzilla":
                components = source.get("components", [])
                if not isinstance(components, list) or not all(isinstance(item, str) and item for item in components):
                    raise ConfigError("bugzilla source components must be a list of strings")
                if "assigned_to" in source and not isinstance(source["assigned_to"], str):
                    raise ConfigError("bugzilla source assigned_to must be a string")
                for keyword_key in ("keywords", "labels", "exclude_keywords", "exclude_labels"):
                    keywords = source.get(keyword_key, [])
                    if not isinstance(keywords, list) or not all(isinstance(item, str) and item for item in keywords):
                        raise ConfigError(f"bugzilla source {keyword_key} must be a list of strings")
                for group in source.get("groups", []):
                    group_value = next(iter(group.values())) if isinstance(group, dict) and group else {}
                    if isinstance(group_value, dict) and "query" in group_value and not isinstance(group_value["query"], str):
                        raise ConfigError("bugzilla group query must be a string")
            if provider == "jira":
                if "projects" in source and (
                    not isinstance(source["projects"], list)
                    or not all(isinstance(project, str) and project for project in source["projects"])
                ):
                    raise ConfigError("jira source projects must be a list of strings")
                for group in source.get("groups", []):
                    group_value = next(iter(group.values())) if isinstance(group, dict) and group else {}
                    if isinstance(group_value, dict) and "projects" in group_value and (
                        not isinstance(group_value["projects"], list)
                        or not all(isinstance(project, str) and project for project in group_value["projects"])
                    ):
                        raise ConfigError("jira group projects must be a list of strings")
            source_fields = _display_fields(source.get("fields"), f"source {provider}.fields")
            provider_fields = providers[provider].get("fields", {})
            source["_display_fields"] = {
                key: source_fields[key] if key in source_fields else provider_fields.get(key)
                for key in ("show", "hide")
                if key in source_fields or key in provider_fields
            }
            source["groups"] = _normalize_groups(source, f"source {provider}", report_groups)
            for group in source["groups"]:
                global_group = global_groups.get(group["name"], {})
                group["filter"] = {
                    **global_group.get("filter", {}),
                    **group["filter"],
                }
                inherited = dict(report["group_defaults"].get(group["name"], {}))
                inherited.update(global_group.get("display_fields", {}))
                inherited.update(source_fields)
                inherited.update(group["display_fields"])
                inherited.update(provider_fields)
                group["display_fields"] = inherited
    if not sources_found:
        raise ConfigError("at least one source is required")
    if "jira" in providers:
        for source in (source for component in config["components"] for source in component.get("sources", []) if source["provider"] == "jira"):
            for group in source["groups"]:
                projects = group["filter"].get("projects", source.get("projects", providers["jira"].get("projects")))
                group["query"] = jira_query(providers["jira"], group["filter"], projects)
    config.setdefault("mcp", {})
    config["report"].setdefault("title", "@today")
    config["report"].setdefault("fallback_group", None)
    return config


def load_config(path: str | Path | None = None) -> dict:
    config_path = Path(path) if path else DEFAULT_CONFIG
    try:
        with config_path.open(encoding="utf-8") as stream:
            config = yaml.safe_load(stream) or {}
    except OSError as exc:
        raise ConfigError(f"cannot read configuration {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    return validate_config(config)
