---
name: idm-triage-config
description: Guide users through creating or updating an IDM Triage YAML configuration.
license: MIT
metadata:
  version: "1.0"
compatibility: Requires an agent that can ask questions and create or edit YAML files.
---

# IDM Triage Configuration

Use this skill when a user wants to create or update a configuration for the
`idm-triage` skill. Ask focused questions, generate a complete YAML file, and
validate it with the bundled validator. Do not request credentials, tokens, or
passwords; those are supplied through environment variables or the configured
MCP service.

## Workflow

1. Ask for the project or component names.
2. Ask which providers are needed: Jira, Bugzilla, GitHub, Codeberg, or Forgejo.
3. Ask where the configuration should be written. The default is
   `skills/idm-triage/assets/config.yaml`.
4. Ask provider-specific connection and filtering questions.
5. Ask which report groups are needed and in what order.
6. Ask about display fields and automated commenters to suppress.
7. Write the YAML configuration.
8. Validate it with:

```bash
uv run skills/idm-triage/scripts/idm_validate_config.py \
  --config path/to/config.yaml
```

If validation fails, correct the configuration and validate it again. Do not
run provider queries or generate a report unless the user explicitly asks for
that next step.

## Base Configuration

Every configuration normally contains these sections:

```yaml
mcp:
  transport: http
  url: http://127.0.0.1:8080/mcp

providers: {}
groups: {}
components: []

report:
  title: "@today"
  groups: [triage]
```

The MCP section is needed for Jira, Bugzilla, or Google Docs publishing.
Provider URLs and connection types belong under `providers`; secrets do not
belong in YAML.

## Provider Questions

### Jira

Ask for:

- Jira URL, normally `https://redhat.atlassian.net`.
- Jira project or projects, as a list of project keys.
- `assigned_team`.
- Jira custom field mappings, if fields such as CVE, CVSS, stale date, or SLA date are needed.

Example:

```yaml
providers:
  jira:
    connection: mcp
    url: https://redhat.atlassian.net
    assigned_team: rhel-idm-ipa
    projects: [RHEL]
    custom_fields:
      cve_id: customfield_10667
      cvss_score: customfield_10859
      stale_date: customfield_10812
      sladate: customfield_11000
```

Project precedence is `group > source > provider`. Jira custom fields map
logical report names to actual Jira field IDs. Standard fields such as
`duedate` do not require a custom-field mapping.

### Bugzilla

Ask for the Bugzilla URL and optional provider-level product. Ask each source
for zero or more components, an optional assignee, and keyword filters.

```yaml
providers:
  bugzilla:
    connection: mcp
    url: https://bugzilla.redhat.com
    product: Fedora

components:
  - name: Fedora IDM
    sources:
      - provider: bugzilla
        components: [freeipa, python-h2]
        assigned_to: developer@example.com
        keywords: [regression]
        exclude_keywords: [duplicate]
        groups:
          - triage: {}
```

`labels` is an alias for `keywords`; `exclude_labels` is an alias for
`exclude_keywords`. Inclusion matches any listed value, exclusions take
precedence, and matching is case-insensitive.

### GitHub, Codeberg, and Forgejo

Ask for the service URL, repository (`owner/name`), and source groups. Ask
whether each group should select issues or pull requests, which states and
labels should be included, which labels should be excluded, and how many days
of history to inspect.

```yaml
providers:
  codeberg:
    connection: http
    url: https://codeberg.org

components:
  - name: FreeIPA
    sources:
      - provider: codeberg
        repository: freeipa/freeipa
        groups:
          - triage:
              state: open
          - test_failures:
              labels: [test-failure]
```

## Shared Groups and Sources

Use top-level `groups` for filters shared by sources. A source references a
group by name and may override individual filter keys. Groups are evaluated
independently, so one item may appear in multiple groups.

```yaml
groups:
  triage:
    state: open
    exclude_labels: [test-failure]
    lookback_days: 14
  test_failures:
    labels: [test-failure]

components:
  - name: FreeIPA
    sources:
      - provider: codeberg
        repository: freeipa/freeipa
        groups:
          - triage: {}
          - test_failures: {}
```

Common filters include `state`, `status`, `is`, `labels`, `exclude_labels`,
`keywords`, `exclude_keywords`, `lookback_days`, `lookahead_days`,
`issue_types`, and Jira `jql`.

## Display Fields

Display settings can be configured at provider, source, group, or report-group
default level:

```yaml
report:
  group_defaults:
    vulnerabilities:
      fields:
        show: [cve_id, cvss_score, duedate, sladate]
        hide: [comments]
```

The precedence is:

```text
provider > group > source > report.group_defaults
```

`show` is a whitelist for optional fields. `hide` suppresses fields. An empty
`show: []` hides optional fields while retaining the identifier, summary, and
URL. Field names are case-insensitive. Any field resolved by a provider can be
used in `show` or `hide`.

For Jira, ask whether custom fields should be shown and ensure each custom
logical name has a field-ID mapping under `providers.jira.custom_fields`.

## Report Settings

Ask for report section order and optional display titles:

```yaml
report:
  title: "@today"
  groups: [triage, vulnerabilities, test_failures, needs_review, autoclose]
  group_titles:
    vulnerabilities: CVEs
  skip_commenters:
    - Tracker AutoManager
    - RHEL Jira Bot
```

Recommend `Tracker AutoManager` and `RHEL Jira Bot` as skipped Jira
commenters to avoid automated-update noise.

## Completion

After writing the file, run the validator and report its result. A valid
configuration can be used with:

```bash
uv run skills/idm-triage/scripts/idm_triage.py \
  --config path/to/config.yaml
```
