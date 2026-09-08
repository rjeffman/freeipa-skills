# IDM Triage

IDM Triage generates configuration-driven Markdown triage reports for identity-management projects by collecting issues from Jira, Bugzilla, GitHub, Codeberg, and Forgejo, applying reusable group filters, retrieving only the fields needed for filtering or reporting, resolving cached user identities, and optionally publishing the resulting report to Google Docs through the configured wtmcp MCP service.

## Running the Skill

Run the default configuration:

```bash
uv run scripts/idm_triage.py
```

Use a different configuration file:

```bash
uv run scripts/idm_triage.py \
  --config path/to/config.yaml
```

Publish a new Google Doc or append to an existing document:

```bash
uv run scripts/idm_triage.py --gdoc
uv run scripts/idm_triage.py --gdoc DOCUMENT_ID_OR_URL
```

The default configuration is `assets/config.yaml`. The
repository also includes a more complete example at
`examples/ipa-config.yml` and an SSSD configuration at
`assets/sssd-config.yaml`.

## Configuration Structure

A configuration is a YAML mapping with these primary sections:

```yaml
mcp:             # Optional MCP connection
providers:       # Named provider connections and provider settings
groups:          # Shared filter defaults
components:      # Project components and their sources
report:          # Report ordering, titles, and display defaults
```

The `mcp` section configures the HTTP MCP endpoint used by Jira, Bugzilla, and
Google Docs integrations:

```yaml
mcp:
  transport: http
  url: http://127.0.0.1:8080/mcp
```

`transport` must be `http`. The URL is optional only when no MCP-backed
provider or Google Docs publishing is used.

## Providers

Each provider must define a connection type and URL. Credentials must not be
stored in YAML.

```yaml
providers:
  jira:
    connection: mcp
    url: https://redhat.atlassian.net
    assigned_team: rhel-idm-ipa
    projects: [RHEL]

  bugzilla:
    connection: mcp
    url: https://bugzilla.redhat.com
    product: Fedora

  github:
    connection: github_api
    url: https://api.github.com

  codeberg:
    connection: http
    url: https://codeberg.org

  forgejo:
    connection: http
    url: https://forgejo.example.com
```

Supported connection types are `mcp`, `github_api`, and `http`.

### Jira

Jira requires `assigned_team`. `projects` is optional and accepts multiple
project keys. When configured, generated JQL contains a `project in (...)`
clause. Project precedence is `group > source > provider`.

```yaml
providers:
  jira:
    connection: mcp
    url: https://redhat.atlassian.net
    assigned_team: rhel-idm-ipa
    projects: [RHEL, FEDORA]
    custom_fields:
      cve_id: customfield_10667
      cvss_score: customfield_10859
      stale_date: customfield_10812
      sla_date: customfield_11000
```

`custom_fields` maps logical report names to Jira field IDs. Standard fields
such as `duedate` do not need a mapping. Custom fields must have a mapping if
they are to be retrieved or displayed. A group's `jql` can replace the
generated query and supports `{assigned_team}`, `{projects}`, `{lookback}`,
`{lookahead}`, `{status}`, and `{issue_types}`.

### Bugzilla

Bugzilla `product` is optional. A source can search multiple components; each
component is queried separately and duplicate bugs are removed:

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
        components: [freeipa, python-h2, python-dns]
        assigned_to: developer@example.com
        keywords: [regression]
        exclude_keywords: [duplicate]
        groups:
          - triage: {}
```

`labels` is an alias for `keywords`, and `exclude_labels` is an alias for
`exclude_keywords`. Inclusion lists match any keyword. Exclusions take
precedence and matching is case-insensitive. The wtmcp Bugzilla plugin must
be configured with its server-side `bugzilla_url`.

### GitHub, Codeberg, and Forgejo

Repository-backed sources require a repository identifier. Group filters can
select issue or pull-request type, state, labels, excluded labels, and a
creation lookback:

```yaml
components:
  - name: FreeIPA
    sources:
      - provider: codeberg
        repository: freeipa/freeipa
        groups:
          - triage: {}
          - test_failures:
              labels: [test-failure]
```

Set `GITHUB_TOKEN` for GitHub and `CODEBERG_TOKEN` for Codeberg or Forgejo
access when required by the remote service.

## Groups and Filters

The top-level `groups` mapping defines shared filter defaults. Sources refer to
groups by name and can override individual filter keys. Every group is
evaluated independently, so an item can belong to multiple groups when it
matches multiple group definitions.

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

Common filters include:

- `state`: `open`, `closed`, or provider-supported equivalent.
- `status`: primarily used by Jira and Bugzilla.
- `is`: `issue` or `pr` for GitHub, Codeberg, and Forgejo.
- `labels`: include items matching any listed label.
- `exclude_labels`: exclude items matching any listed label.
- `keywords`: Bugzilla keyword inclusion filter.
- `exclude_keywords`: Bugzilla keyword exclusion filter.
- `lookback_days`: restrict items by creation date.
- `lookahead_days`: Jira autoclose filtering based on stale date.
- `issue_types`: Jira issue types such as `Bug`, `Story`, or `Vulnerability`.
- `jql`: optional Jira query override.

## Display Fields

Display policies can be configured at provider, source, group, or report-group
default level:

```yaml
providers:
  bugzilla:
    fields:
      hide: [comments]

components:
  - name: FreeIPA
    sources:
      - provider: bugzilla
        fields:
          show: [component, status]
        groups:
          - triage:
              fields:
                show: [component, severity]

report:
  group_defaults:
    vulnerabilities:
      fields:
        show: [cve_id, cvss_score, duedate, sladate]
```

Display precedence is:

```text
provider > group > source > report.group_defaults
```

`show` is a whitelist for optional fields. `hide` suppresses selected fields.
An empty `show: []` hides all optional fields while retaining the identifier,
summary, and URL. Field names are case-insensitive, and any field resolved by
the provider can be used. Arbitrary Jira fields become available through
`custom_fields` mappings and are rendered with a generated label.

Field retrieval is demand-driven. The collector requests fields needed by
filters, display policies, and configured custom-field mappings rather than
fetching every available field. Comments and other large detail sections are
also requested only when needed.

## Report Configuration

`report.groups` controls section order and must list every group referenced by a
source or global group definition. `group_titles` changes displayed section
names. `title` is rendered at the top of the Markdown report, and
`fallback_group` can receive items that have no explicitly matched group.

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

These two Jira automation users are recommended skip-commenter entries so
automated updates do not add noise to report authors and comments.

Triage-style sections are grouped by component and provider. This applies to
items retain the flat autoclose layout and may show a normalized stale date.

## User Cache

Resolved display names are stored in the ignored,
human-editable `assets/user_map_cache.yaml` file. Cache entries are separated
by provider:

```yaml
users:
  codeberg:
    flo-renaud:
      name: Florence Renaud
      mention: false
  jira:
    jira-bot@example.com:
      name: Jira Bot
      mention: false
```

Known users render as `@Name` when mentions are enabled. Set `mention: false`
to render the name without an `@` prefix. Email-only values remain plain text.

## Configuration Validation

Validate the default configuration without collecting remote data:

```bash
uv run skills/idm-triage/scripts/idm_validate_config.py
```

Validate a specific configuration:

```bash
uv run skills/idm-triage/scripts/idm_validate_config.py \
  --config path/to/config.yaml
```

The validator exits with status `0` for a valid configuration and status `2`
for an invalid configuration. It only parses and validates YAML; it does not
contact Jira, Bugzilla, GitHub, Codeberg, Forgejo, or the MCP service.
```

Run the test suite:

```bash
python3 -m unittest discover -s scripts/tests -q
```
