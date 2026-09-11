---
name: idm-triage
description: Generate an IDM triage report and display it, save it as Markdown, or publish it to Google Docs.
license: MIT
metadata:
  author: Rafael Guterres Jeffman <rjeffman@redhat.com>
  version: "1.0"
compatibility: Requires Python 3.10+, requests, PyYAML, and the configured provider credentials or MCP service.
allowed-tools: Read
---

# IDM Triage

Generate a configuration-driven triage report from Jira, Bugzilla, GitHub,
Codeberg, and Forgejo. The report is rendered as Markdown and can be printed
to the console, redirected to a file, or written to a Google Doc through the
configured MCP service. The default output mode is the console.

## Arguments

The skill accepts these output arguments:

```text
--file PATH       Save the Markdown report to PATH
--gdoc [ID_OR_URL]  Create a Google Doc, or append to an existing document
```

The output modes are mutually exclusive. If neither argument is supplied, the
Markdown report is printed to standard output.

`--file` is a skill-level output option. The underlying report command writes
the report to standard output, so the skill saves it using shell redirection.
`--gdoc` is passed to the report command and uses the configured MCP service.

## Commands

Default console output:

```bash
uv run skills/idm-triage/scripts/idm_triage.py
```

Save the report to a Markdown file:

```bash
uv run skills/idm-triage/scripts/idm_triage.py \
  > /path/to/triage-report.md
```

When handling `--file /path/to/triage-report.md`, use the equivalent command
above and do not pass `--file` to `idm_triage.py`.

Create a new Google Doc:

```bash
uv run skills/idm-triage/scripts/idm_triage.py --gdoc
```

Append to an existing Google Doc by ID or URL:

```bash
uv run skills/idm-triage/scripts/idm_triage.py --gdoc DOCUMENT_ID_OR_URL
```

Use a specific configuration with any output mode:

```bash
uv run skills/idm-triage/scripts/idm_triage.py \
  --config path/to/config.yaml

uv run skills/idm-triage/scripts/idm_triage.py \
  --config path/to/config.yaml \
  --gdoc

uv run skills/idm-triage/scripts/idm_triage.py \
  --config path/to/config.yaml \
  > /path/to/triage-report.md
```

The default configuration is `skills/idm-triage/assets/config.yaml`.

## Configuration

The YAML configuration contains:

- `mcp`: optional MCP HTTP connection, normally `http://127.0.0.1:8080/mcp`.
- `providers`: provider URLs, connection types, Jira settings, and Bugzilla settings.
- `groups`: shared filter defaults inherited by source groups.
- `components`: project components and their provider sources.
- `report`: report ordering, titles, display defaults, and skipped commenters.

Example provider configuration:

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
      sladate: customfield_11000

  bugzilla:
    connection: mcp
    url: https://bugzilla.redhat.com
    product: Fedora

  github:
    connection: github_api
    url: https://api.github.com
```

Jira `projects` accepts multiple project keys. Jira project precedence is
`group > source > provider`. Jira custom fields map logical report names to
actual Jira field IDs. Standard fields such as `duedate` do not need a custom
mapping.

Bugzilla `product` is optional. Bugzilla sources can define multiple
`components`, `assigned_to`, `keywords`, and `exclude_keywords`. `labels` is
an alias for `keywords`, and `exclude_labels` is an alias for
`exclude_keywords`. Inclusion matches any value; exclusions take precedence.

Example shared groups and sources:

```yaml
groups:
  triage:
    state: open
    exclude_labels: [test-failure]
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

Groups are evaluated independently. An item can appear in multiple groups if
it matches multiple group filters. Source-level filters apply to all groups in
that source, while source group values override matching shared filter keys.

## Display Fields

Display policies can be configured at provider, source, group, or report-group
default level:

```yaml
report:
  group_defaults:
    vulnerabilities:
      fields:
        show: [cve_id, cvss_score, duedate, sladate]
        hide: [comments]
```

Display precedence is:

```text
provider > group > source > report.group_defaults
```

`show` is a whitelist for optional fields. `hide` suppresses selected fields.
An empty `show: []` hides all optional fields while retaining the identifier,
summary, and URL. Field names are case-insensitive, and any field resolved by
a provider can be used.

## Validation

Validate a configuration without contacting providers:

```bash
uv run skills/idm-triage/scripts/idm_validate_config.py
uv run skills/idm-triage/scripts/idm_validate_config.py \
  --config path/to/config.yaml
```

The validator exits with status `0` for a valid configuration and status `2`
for an invalid configuration.

## Credentials and Cache

Set `GITHUB_TOKEN` for GitHub and `CODEBERG_TOKEN` for Codeberg or Forgejo.
Jira and Bugzilla credentials are managed by the configured MCP service. The
Bugzilla wtmcp plugin must have its server-side `bugzilla_url` configured.

Resolved provider names are stored in the ignored,
human-editable `skills/idm-triage/assets/user_map_cache.yaml` file under a
provider-specific namespace.

For more configuration details, see `skills/idm-triage/README.md`.
