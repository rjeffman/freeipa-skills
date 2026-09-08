"""Provider-neutral display-name lookup backed by an editable YAML cache."""

from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass
from typing import Callable

import yaml


DEFAULT_CACHE = Path(__file__).parent.parent / "assets" / "user_map_cache.yaml"


@dataclass(frozen=True)
class CachedUser:
    name: str
    mention: bool = True


class UserCache:
    """Resolve provider users without expiring cached names."""

    def __init__(self, lookup: Callable[[str], str | None], path: Path = DEFAULT_CACHE):
        self.lookup = lookup
        self.path = path
        self.users = self._load()

    def _load(self) -> dict[str, dict[str, CachedUser]]:
        try:
            with self.path.open(encoding="utf-8") as stream:
                data = yaml.safe_load(stream) or {}
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict) or not isinstance(data.get("users", {}), dict):
            return {}
        users = data["users"]
        # Migrate the previous flat Jira-only cache format in memory.
        if users and all(isinstance(value, str) for value in users.values()):
            users = {"jira": users}
        result = {}
        for provider, entries in users.items():
            if not isinstance(entries, dict):
                continue
            result[str(provider)] = {}
            for identifier, entry in entries.items():
                if isinstance(entry, str):
                    result[str(provider)][str(identifier)] = CachedUser(entry)
                elif isinstance(entry, dict) and isinstance(entry.get("name"), str):
                    result[str(provider)][str(identifier)] = CachedUser(
                        entry["name"], bool(entry.get("mention", True)),
                    )
        return result

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as stream:
            yaml.safe_dump(
                {
                    "users": {
                        provider: {
                            identifier: {"name": entry.name, "mention": entry.mention}
                            for identifier, entry in sorted(entries.items())
                        }
                        for provider, entries in sorted(self.users.items())
                    }
                },
                stream,
                sort_keys=False,
            )

    def resolve(
        self,
        provider: str,
        identifier: str,
        should_lookup: bool = True,
    ) -> CachedUser:
        entries = self.users.setdefault(provider, {})
        if identifier in entries:
            return entries[identifier]
        if not should_lookup:
            return CachedUser(identifier)
        name = self.lookup(identifier)
        if not name:
            return CachedUser(identifier)
        entry = CachedUser(name)
        entries[identifier] = entry
        self._save()
        return entry


def jira_display_name(call_tool: Callable[[str, dict], str | None], email: str) -> str | None:
    """Look up a Jira user's display name using their email as the username."""
    text = call_tool("jira_get_user", {"username": email})
    if not text:
        return None
    match = re.search(r"^displayName:\s+(.+)$", text, re.MULTILINE)
    return match.group(1).strip().strip('"') if match else None
