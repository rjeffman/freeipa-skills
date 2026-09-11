"""Provider-neutral data structures used by triage collection and reporting."""

from dataclasses import dataclass, field


@dataclass
class Item:
    component: str
    provider: str
    repository: str
    roles: list[str]
    item_type: str
    identifier: str
    url: str
    summary: str
    author: str = ""
    labels: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    matched_labels: list[str] = field(default_factory=list)

    @property
    def number_or_key(self) -> str:
        return self.identifier


def classify(item: Item, label_groups: dict, default_groups: list[str] | None = None) -> Item:
    groups = list(default_groups or [])
    matched_labels = []
    for label, configured_groups in label_groups.items():
        matching_labels = [actual for actual in item.labels if label.casefold() == actual.casefold()]
        if matching_labels:
            matched_labels.extend(matching_labels)
            if isinstance(configured_groups, str):
                configured_groups = [configured_groups]
            for group in configured_groups:
                if group not in groups:
                    groups.append(group)
    item.groups = groups
    item.matched_labels = list(dict.fromkeys(matched_labels))
    return item
