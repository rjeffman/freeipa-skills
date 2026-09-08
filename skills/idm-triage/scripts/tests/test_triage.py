import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from config import ConfigError, validate_config
from models import Item, classify
from providers import _bugzilla_values, _date_only, _github_item_url, _groups_for, _item, _resolve_cached_author, _stale_date
from retrieve_bugzilla_tickets import _parse_records
from report import render
from user_cache import UserCache
from idm_triage_issues import _parse_comment_text


def base_config():
    return {
        "mcp": {"transport": "http", "url": "http://localhost:8080/mcp"},
        "providers": {
            "jira": {"assigned_team": "team", "connection": "mcp", "url": "https://jira.example"},
            "github": {"connection": "github_api", "url": "https://api.github.com"},
        },
        "components": [{"name": "Project", "sources": [{"provider": "github", "repository": "o/r", "groups": [{"review": {"filter": {"is": "issue"}}}]}]}],
        "report": {"groups": ["review", "failures"]},
    }


class ConfigTests(unittest.TestCase):
    def test_jira_group_query_uses_provider_settings(self):
        config = base_config()
        config["components"][0]["sources"][0] = {
            "provider": "jira",
            "groups": [{"review": {"status": "New", "lookback_days": 14}}],
        }
        config = validate_config(config)
        self.assertIn('AssignedTeam[Dropbox] = "team"', config["components"][0]["sources"][0]["groups"][0]["query"])

    def test_jira_project_uses_provider_setting(self):
        config = base_config()
        config["providers"]["jira"]["projects"] = ["IPA", "RHEL"]
        config["components"][0]["sources"][0] = {
            "provider": "jira", "groups": [{"review": {"status": "New"}}],
        }
        validated = validate_config(config)
        self.assertIn('project in ("IPA", "RHEL")', validated["components"][0]["sources"][0]["groups"][0]["query"])

    def test_jira_project_precedence_is_group_source_provider(self):
        config = base_config()
        config["providers"]["jira"]["projects"] = ["ProviderProject"]
        config["components"][0]["sources"][0] = {
            "provider": "jira", "projects": ["SourceProject"],
            "groups": [{"review": {"projects": ["GroupProject"]}}],
        }
        validated = validate_config(config)
        query = validated["components"][0]["sources"][0]["groups"][0]["query"]
        self.assertIn('project in ("GroupProject")', query)
        self.assertNotIn('project in ("SourceProject")', query)

    def test_jira_autoclose_project_is_not_hardcoded(self):
        config = base_config()
        config["components"][0]["sources"][0] = {
            "provider": "jira", "groups": [{"autoclose": {"lookahead_days": 30}}],
        }
        config["report"]["groups"] = ["autoclose"]
        query = validate_config(config)["components"][0]["sources"][0]["groups"][0]["query"]
        self.assertNotIn("project = RHEL", query)

    def test_global_group_filters_are_merged_into_sources(self):
        config = base_config()
        config["report"]["groups"] = ["triage", "test_failures"]
        config["groups"] = {
            "triage": {"exclude_labels": ["test-failure"], "state": "open"},
            "test_failures": {"labels": ["test-failure"]},
        }
        config["components"][0]["sources"] = [{
            "provider": "github", "repository": "o/r",
            "groups": [{"triage": {}}, {"test_failures": {}}],
        }]
        validated = validate_config(config)
        source = validated["components"][0]["sources"][0]
        self.assertEqual(_groups_for({"state": "open", "labels": [{"name": "test-failure"}]}, "issue", source), ["test_failures"])

    def test_legacy_jira_configuration_is_invalid(self):
        config = base_config()
        config["jira"] = {"assigned_team": "team"}
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_jira_group_filter_wrapper_is_invalid(self):
        config = base_config()
        config["components"][0]["sources"][0] = {
            "provider": "jira",
            "groups": [{"review": {"filter": {"status": "New"}}}],
        }
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_unknown_group_is_invalid(self):
        config = base_config()
        config["components"][0]["sources"][0]["groups"] = [{"missing": {}}]
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_custom_jql_placeholders(self):
        config = base_config()
        config["components"][0]["sources"][0]["provider"] = "jira"
        config["components"][0]["sources"][0]["groups"] = [{"review": {"jql": "team = {assigned_team} AND created >= {lookback}"}}]
        config = validate_config(config)
        self.assertEqual(config["components"][0]["sources"][0]["groups"][0]["query"], "team = team AND created >= 2w")

    def test_multiple_groups_are_valid(self):
        config = base_config()
        config["components"][0]["sources"][0]["groups"] = [{"review": {}}, {"failures": {}}]
        validate_config(config)

    def test_invalid_source_lookback_is_rejected(self):
        config = base_config()
        config["components"][0]["sources"][0]["groups"][0]["review"]["filter"]["lookback_days"] = 0
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_bugzilla_product_is_provider_level(self):
        config = base_config()
        config["providers"]["bugzilla"] = {"connection": "mcp", "url": "https://bugzilla.example", "product": "FreeIPA"}
        config["components"][0]["sources"] = [{"provider": "bugzilla", "components": ["IPA", "Web UI"], "assigned_to": "dev@example.com", "keywords": ["regression"], "groups": [{"review": {"status": "NEW"}}]}]
        validated = validate_config(config)
        source = validated["components"][0]["sources"][0]
        self.assertEqual(source["components"], ["IPA", "Web UI"])
        self.assertEqual(source["assigned_to"], "dev@example.com")

    def test_bugzilla_product_is_optional(self):
        config = base_config()
        config["providers"]["bugzilla"] = {"connection": "mcp", "url": "https://bugzilla.example"}
        config["components"][0]["sources"] = [{"provider": "bugzilla", "groups": [{"review": {}}]}]
        validate_config(config)

    def test_bugzilla_source_lists_are_validated(self):
        config = base_config()
        config["providers"]["bugzilla"] = {"connection": "mcp", "url": "https://bugzilla.example", "product": "FreeIPA"}
        config["components"][0]["sources"] = [{"provider": "bugzilla", "keywords": "regression", "groups": [{"review": {}}]}]
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_bugzilla_label_keyword_aliases_are_validated(self):
        config = base_config()
        config["providers"]["bugzilla"] = {"connection": "mcp", "url": "https://bugzilla.example"}
        config["components"][0]["sources"] = [{
            "provider": "bugzilla",
            "labels": ["regression"],
            "exclude_keywords": ["duplicate"],
            "groups": [{"review": {"exclude_labels": ["wontfix"]}}],
        }]
        validate_config(config)

    def test_display_fields_can_be_configured_per_provider_and_source(self):
        config = base_config()
        config["providers"]["bugzilla"] = {
            "connection": "mcp", "url": "https://bugzilla.example",
            "fields": {"hide": ["comments"]},
        }
        config["components"][0]["sources"] = [{
            "provider": "bugzilla", "fields": {"show": ["component", "status"]},
            "groups": [{"review": {}}],
        }]
        validated = validate_config(config)
        self.assertEqual(validated["components"][0]["sources"][0]["_display_fields"], {
            "show": ["component", "status"], "hide": ["comments"],
        })

    def test_display_fields_are_supported_for_github_sources(self):
        config = base_config()
        config["providers"]["github"]["fields"] = {"hide": ["comments"]}
        config["components"][0]["sources"][0]["fields"] = {"show": ["status", "labels"]}
        validated = validate_config(config)
        self.assertEqual(
            validated["components"][0]["sources"][0]["_display_fields"],
            {"show": ["status", "labels"], "hide": ["comments"]},
        )

    def test_due_and_sla_date_fields_are_supported(self):
        config = base_config()
        config["providers"]["github"]["fields"] = {"show": ["duedate", "sladate"]}
        validate_config(config)

    def test_display_field_names_are_case_insensitive_and_group_show_is_supported(self):
        config = base_config()
        config["components"][0]["sources"][0]["groups"] = [{"review": {"show": ["Assignee", "Keywords"]}}]
        validated = validate_config(config)
        group = validated["components"][0]["sources"][0]["groups"][0]
        self.assertEqual(group["display_fields"]["show"], ["assignee", "keywords"])

    def test_group_display_fields_override_source_fields(self):
        config = base_config()
        config["providers"]["bugzilla"] = {"connection": "mcp", "url": "https://bugzilla.example"}
        config["components"][0]["sources"] = [{
            "provider": "bugzilla", "fields": {"hide": ["comments"]},
            "groups": [{"review": {"fields": {"show": ["component", "comments"]}}}],
        }]
        validated = validate_config(config)
        group = validated["components"][0]["sources"][0]["groups"][0]
        self.assertEqual(group["display_fields"], {"show": ["component", "comments"], "hide": ["comments"]})

    def test_global_group_defaults_are_inherited(self):
        config = base_config()
        config["report"]["group_defaults"] = {
            "review": {"fields": {"hide": ["comments"]}},
        }
        config["providers"]["bugzilla"] = {"connection": "mcp", "url": "https://bugzilla.example"}
        config["components"][0]["sources"] = [{
            "provider": "bugzilla", "groups": [{"review": {}}],
        }]
        validated = validate_config(config)
        group = validated["components"][0]["sources"][0]["groups"][0]
        self.assertEqual(group["display_fields"], {"hide": ["comments"]})

    def test_global_group_default_unknown_group_is_rejected(self):
        config = base_config()
        config["report"]["group_defaults"] = {"missing": {"fields": {"hide": ["comments"]}}}
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_provider_display_fields_have_highest_precedence(self):
        config = base_config()
        config["providers"]["bugzilla"] = {
            "connection": "mcp", "url": "https://bugzilla.example",
            "fields": {"show": ["labels"]},
        }
        config["report"]["group_defaults"] = {"review": {"fields": {"show": ["comments"]}}}
        config["components"][0]["sources"] = [{
            "provider": "bugzilla", "fields": {"show": ["component"]},
            "groups": [{"review": {"fields": {"show": ["status"]}}}],
        }]
        validated = validate_config(config)
        group = validated["components"][0]["sources"][0]["groups"][0]
        self.assertEqual(group["display_fields"]["show"], ["labels"])

    def test_arbitrary_display_field_name_is_accepted(self):
        config = base_config()
        config["providers"]["bugzilla"] = {
            "connection": "mcp", "url": "https://bugzilla.example",
            "fields": {"show": ["unknown"]},
        }
        validate_config(config)


class ModelAndReportTests(unittest.TestCase):
    def test_bugzilla_keywords_accept_any_case_insensitive_match(self):
        self.assertEqual(_bugzilla_values(["Regression", "release-blocker"]), ["Regression", "release-blocker"])
        wanted = {"regression", "release-blocker"}
        actual = {value.casefold() for value in _bugzilla_values("foo, Regression")}
        self.assertTrue(wanted.intersection(actual))

    def test_bugzilla_table_response_is_parsed(self):
        text = 'bugs[2]{id,summary,keywords}:\n1,"First","regression,foo"\n2,"Second","bar"'
        self.assertEqual(_parse_records(text, "bugs")[0]["id"], "1")
        self.assertEqual(_parse_records(text, "bugs")[0]["keywords"], "regression,foo")

    def test_bugzilla_yaml_response_is_parsed(self):
        text = """bugs[1]:
  - component[1]: python-h2
    id: 2506577
    keywords[2]: FutureFeature,Triaged
    status: NEW
"""
        bug = _parse_records(text, "bugs")[0]
        self.assertEqual(bug["component"], "python-h2")
        self.assertEqual(bug["id"], 2506577)

    @patch("retrieve_bugzilla_tickets.call_tool")
    def test_bugzilla_search_passes_provider_and_source_filters(self, call_tool):
        call_tool.return_value = 'bugs[1]{id,summary}:\n1,"Bug"'
        from retrieve_bugzilla_tickets import search_bugzilla
        search_bugzilla("FreeIPA", "IPA", "dev@example.com", "NEW")
        arguments = call_tool.call_args.args[1]
        self.assertEqual(arguments["product"], "FreeIPA")
        self.assertEqual(arguments["component"], "IPA")
        self.assertEqual(arguments["assigned_to"], "dev@example.com")
        self.assertEqual(arguments["status"], "NEW")

    @patch("retrieve_bugzilla_tickets.call_tool")
    def test_bugzilla_search_omits_unconfigured_product(self, call_tool):
        call_tool.return_value = 'bugs[0]{id,summary}:'
        from retrieve_bugzilla_tickets import search_bugzilla
        search_bugzilla(component="IPA")
        self.assertNotIn("product", call_tool.call_args.args[1])

    @patch("providers._ensure_mcp_session", return_value=True)
    @patch("retrieve_bugzilla_tickets.get_comments", return_value=[])
    @patch("retrieve_bugzilla_tickets.search_bugzilla")
    def test_bugzilla_collector_searches_components_deduplicates_and_filters_keywords(self, search, _comments, _session):
        search.side_effect = [
            [{"id": "1", "summary": "match", "keywords": ["Regression"], "reporter": "a@example.com"}],
            [
                {"id": "1", "summary": "match", "keywords": ["Regression"], "reporter": "a@example.com"},
                {"id": "2", "summary": "skip", "keywords": ["other"], "reporter": "b@example.com"},
            ],
        ]
        from providers import collect_bugzilla
        source = {
            "provider": "bugzilla",
            "components": ["IPA", "Web UI"],
            "assigned_to": "dev@example.com",
            "keywords": ["regression"],
            "groups": [{"name": "review", "filter": {"status": "NEW"}}],
        }
        items = collect_bugzilla(
            {"name": "FreeIPA"}, source,
            {"mcp": {"url": "http://localhost:8080/mcp"}, "providers": {"bugzilla": {"product": "FreeIPA", "url": "https://bugzilla.example"}}},
        )
        self.assertEqual([item.identifier for item in items], ["1"])
        self.assertEqual(search.call_count, 2)
        self.assertEqual(search.call_args_list[0].kwargs["component"], "IPA")
        self.assertEqual(search.call_args_list[1].kwargs["component"], "Web UI")

    @patch("providers._ensure_mcp_session", return_value=True)
    @patch("retrieve_bugzilla_tickets.get_comments", return_value=[])
    @patch("retrieve_bugzilla_tickets.search_bugzilla", return_value=[
        {"id": "1", "summary": "keep", "keywords": ["Regression"]},
        {"id": "2", "summary": "exclude", "keywords": ["Regression", "Duplicate"]},
    ])
    def test_bugzilla_labels_and_keywords_are_include_exclude_aliases(self, _search, _comments, _session):
        from providers import collect_bugzilla
        source = {
            "provider": "bugzilla", "labels": ["regression"],
            "exclude_keywords": ["duplicate"],
            "groups": [{"name": "review", "filter": {}}],
        }
        items = collect_bugzilla(
            {"name": "FreeIPA"}, source,
            {"mcp": {"url": "http://localhost:8080/mcp"}, "providers": {"bugzilla": {"url": "https://bugzilla.example"}}},
        )
        self.assertEqual([item.identifier for item in items], ["1"])

    def test_configured_fields_show_component_and_hide_comments(self):
        item = _item(
            {"name": "Fedora"},
            {"provider": "bugzilla", "_display_fields": {"show": ["component"], "hide": ["comments"]}},
            {"number": "1", "url": "url", "summary": "Bug", "component": "python-h2",
             "comments": [{"author": "user", "text": "comment"}]},
            "issue", ["triage"],
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["triage"]}})
        self.assertIn("**Component:** python-h2", output)
        self.assertNotIn("comment", output)

    def test_group_display_fields_are_used_when_rendering(self):
        item = Item(
            "Fedora", "bugzilla", "", [], "issue", "1", "url", "Bug",
            comments=[{"author": "user", "text": "comment"}],
            groups=["triage", "investigation"],
            metadata={"component": "python-h2", "_display_fields_by_group": {
                "triage": {"show": ["component"], "hide": ["comments"]},
                "investigation": {"show": ["component", "comments"]},
            }},
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["triage", "investigation"]}})
        self.assertEqual(output.count("comment"), 1)

    def test_provider_normalizes_state_as_status_for_display(self):
        item = _item(
            {"name": "Project"},
            {"provider": "github", "groups": [{"name": "triage", "display_fields": {"show": ["status"]}}]},
            {"number": 1, "url": "url", "summary": "Issue", "state": "open"},
            "issue", ["triage"],
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["triage"]}})
        self.assertIn("**Status:** open", output)

    def test_github_assignee_uses_login_and_cached_name(self):
        item = _item(
            {"name": "Project"},
            {"provider": "github", "groups": [{"name": "triage", "display_fields": {"show": ["assignee"]}}]},
            {"number": 1, "url": "url", "summary": "Issue", "assignee": {"login": "octocat"}},
            "issue", ["triage"],
        )
        self.assertEqual(item.metadata["assignee"], "octocat")
        with tempfile.TemporaryDirectory() as directory:
            cache = UserCache(lambda _: "Octo Cat", Path(directory) / "users.yml")
            cache.resolve("github", "octocat")
            _resolve_cached_author(item, cache)
        self.assertEqual(item.metadata["assignee"], "Octo Cat")

    def test_provider_normalizes_due_and_sla_dates_for_display(self):
        item = _item(
            {"name": "Project"},
            {"provider": "github", "groups": [{"name": "triage", "display_fields": {"show": ["duedate", "sladate"]}}]},
            {"number": 1, "url": "url", "summary": "Issue", "due_date": "2026-10-01", "sla_date": "2026-10-15"},
            "issue", ["triage"],
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["triage"]}})
        self.assertIn("**Due Date:** 2026-10-01", output)
        self.assertIn("**SLA Date:** 2026-10-15", output)

    def test_date_fields_render_only_the_date(self):
        self.assertEqual(_date_only("2026-10-19T10:19:00.000+0000"), "2026-10-19")
        self.assertIsNone(_date_only("rfv1:k1:opaque-sla-value"))

    def test_jira_detail_fields_include_due_and_sla_dates(self):
        import idm_triage_issues as issue_module
        from providers import collect_jira

        config = {
            "mcp": {"url": "http://localhost:8080/mcp"},
            "providers": {"jira": {"assigned_team": "team", "url": "https://jira.example", "custom_fields": {}}},
        }
        source = {
            "provider": "jira",
            "groups": [{
                "name": "triage", "filter": {}, "query": "project = X",
                "display_fields": {"show": ["duedate", "sladate"]},
            }],
        }
        with patch("providers._ensure_mcp_session", return_value=True), \
             patch("retrieve_jira_tickets.search_jira", return_value=[]):
            collect_jira({"name": "Project"}, source, config)
        self.assertEqual(issue_module.CUSTOM_FIELDS["duedate"], "duedate")
        self.assertEqual(issue_module.CUSTOM_FIELDS["sladate"], "sladate")

    def test_jira_detail_fields_are_not_requested_without_display_or_filter_use(self):
        from providers import _jira_field_request
        mapping, include_comments, include_versions = _jira_field_request(
            {"filter": {}, "display_fields": {"show": []}},
            {},
        )
        self.assertNotIn("duedate", mapping)
        self.assertNotIn("sladate", mapping)
        self.assertFalse(include_comments)
        self.assertFalse(include_versions)

    def test_arbitrary_retrieved_field_can_be_rendered(self):
        item = Item(
            "Project", "jira", "", [], "issue", "1", "url", "Issue",
            groups=["triage"], metadata={
                "custom_field": "value",
                "_display_fields_by_group": {"triage": {"show": ["custom_field"]}},
            },
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["triage"]}})
        self.assertIn("**Custom Field:** value", output)
    def test_multiple_labels_and_groups(self):
        item = Item("P", "github", "o/r", ["issues"], "issue", "1", "url", "summary", labels=["A", "B"])
        classify(item, {"a": ["review"], "b": ["failures"]})
        self.assertEqual(item.groups, ["review", "failures"])

    def test_labels_matching_report_groups_are_hidden(self):
        item = Item("P", "github", "o/r", ["pull_requests"], "pull_request", "1", "url", "summary", labels=["Waiting for review", "urgent"])
        classify(item, {"Waiting for review": ["needs_review"]})
        output = render([item], {"report": {"title": "Custom", "groups": ["needs_review"]}})
        self.assertNotIn("Waiting for review", output)
        self.assertIn("urgent", output)

    def test_report_order_and_title(self):
        items = [
            Item("P", "github", "o/r", ["issues"], "issue", "1", "url", "one", groups=["failures"]),
            Item("P", "github", "o/r", ["issues"], "issue", "2", "url", "two", groups=["review"]),
        ]
        output = render(items, {"report": {"title": "Custom", "groups": ["review", "failures"]}})
        self.assertLess(output.index("Review"), output.index("Failures"))
        self.assertTrue(output.startswith("## Custom"))

    def test_vulnerabilities_group_is_titled_cves(self):
        item = Item("P", "jira", "", ["issues"], "issue", "RHEL-1", "url", "CVE", groups=["vulnerabilities"])
        output = render([item], {"report": {"title": "Custom", "groups": ["vulnerabilities"], "group_titles": {"vulnerabilities": "CVEs"}}})
        self.assertIn("**CVEs**", output)
        self.assertNotIn("**Vulnerabilities**", output)

    def test_group_titles_are_configurable(self):
        item = Item("P", "github", "o/r", ["issues"], "issue", "1", "url", "item", groups=["review"])
        output = render([item], {"report": {"title": "Custom", "groups": ["review"], "group_titles": {"review": "Ready for Review"}}})
        self.assertIn("**Ready for Review**", output)

    def test_triage_is_subdivided_by_provider_with_jira_first(self):
        items = [
            Item("P", "github", "o/r", ["issues"], "issue", "2", "url", "github", groups=["triage"]),
            Item("P", "jira", "", ["issues"], "issue", "RHEL-1", "url", "jira", groups=["triage"]),
            Item("P", "codeberg", "o/r", ["issues"], "issue", "1", "url", "codeberg", groups=["triage"]),
        ]
        output = render(items, {"report": {"title": "Custom", "groups": ["triage"]}})
        self.assertIn("- **P**", output)
        self.assertLess(output.index("    - **Jira**"), output.index("    - **GitHub**"))
        self.assertLess(output.index("    - **GitHub**"), output.index("    - **Codeberg**"))
        self.assertIn("        - [RHEL-1]", output)

    def test_source_sections_are_subdivided_by_component_and_provider(self):
        items = [
            Item("FreeIPA", "github", "freeipa/freeipa", [], "issue", "1", "url", "failure", groups=["test_failures"]),
            Item("FreeIPA", "bugzilla", "", [], "issue", "2", "url", "cve", groups=["vulnerabilities"]),
            Item("WebUI", "github", "freeipa/freeipa-webui", [], "issue", "3", "url", "review", groups=["needs_review"]),
        ]
        output = render(items, {"report": {"title": "Custom", "groups": ["test_failures", "needs_review", "vulnerabilities"]}})
        self.assertIn("**Test Failures**\n\n- **FreeIPA**\n    - **GitHub**", output)
        self.assertIn("**Needs Review**\n\n- **WebUI**\n    - **GitHub**", output)
        self.assertIn("**Vulnerabilities**\n\n- **FreeIPA**\n    - **Bugzilla**", output)

    def test_nested_report_lines_are_indented_bullets(self):
        item = Item(
            "P", "github", "o/r", ["issues"], "issue", "1", "url", "one",
            author="author", labels=["label"], comments=[{"author": "commenter", "text": "text"}],
            groups=["review"],
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["review"]}})
        self.assertIn("    - **Author:** author", output)
        self.assertIn("    - **Labels:** label", output)
        self.assertNotIn("*   *", output)

    def test_test_failure_label_is_hidden_in_test_failure_group(self):
        item = Item(
            "P", "codeberg", "o/r", ["issues"], "issue", "1", "url", "failure",
            labels=["test-failure", "tests"], groups=["test_failures"],
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["test_failures"]}})
        self.assertNotIn("test-failure", output)
        self.assertIn("tests", output)

    def test_stale_date_is_normalized(self):
        self.assertEqual(_stale_date({"customfield_10812": "2026-09-30T00:00:00.000+0000"}, "customfield_10812"), "2026-09-30")
        self.assertEqual(_stale_date({"Stale Date[Date]": "2026-10-01"}, "customfield_10812"), "2026-10-01")

    def test_autoclose_group_renders_stale_date(self):
        item = Item(
            "P", "jira", "", ["issues"], "issue", "RHEL-1", "url", "summary",
            groups=["autoclose"], metadata={"stale_date": "2026-09-30"},
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["autoclose"]}})
        self.assertIn("**Autoclose**", output)
        self.assertIn("Closes at: 2026-09-30", output)

    def test_autoclose_date_is_first_nested_entry_line(self):
        item = Item(
            "P", "jira", "", ["issues"], "issue", "RHEL-1", "url", "summary",
            author="author", groups=["autoclose"], metadata={"stale_date": "2026-09-30"},
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["autoclose"]}})
        entry = output[output.index("- [RHEL-1"):]
        self.assertLess(entry.index("Closes at"), entry.index("Author"))

    def test_autoclose_comment_user_marker_is_removed(self):
        item = Item(
            "P", "jira", "", ["issues"], "issue", "RHEL-1", "url", "summary",
            groups=["autoclose"], comments=[{"author": "Tracker", "text": "@SFDC SYSTEM USER updated this issue."}],
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["autoclose"]}})
        self.assertIn("SFDC SYSTEM USER updated this issue.", output)
        self.assertNotIn("@SFDC SYSTEM USER", output)

    def test_summary_and_comment_tabs_are_expanded(self):
        item = Item(
            "P", "jira", "", ["issues"], "issue", "RHEL-1", "url", "summary\ttext",
            groups=["triage"], comments=[{"author": "User", "text": "comment\ttext"}],
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["triage"]}})
        self.assertIn("summary    text", output)
        self.assertIn("comment    text", output)
        self.assertNotIn("\t", output)

    def test_stale_date_is_not_rendered_outside_autoclose(self):
        item = Item(
            "P", "jira", "", ["issues"], "issue", "RHEL-1", "url", "summary",
            groups=["triage"], metadata={"stale_date": "2026-09-30"},
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["triage"]}})
        self.assertNotIn("Closes at", output)

    def test_jira_comments_render_mentions(self):
        item = Item(
            "P", "jira", "", ["issues"], "issue", "RHEL-1", "url", "summary",
            groups=["triage"], comments=[{"author": "Rafael Jeffman", "email": "rjeffman@example.com", "text": "Please review."}],
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["triage"], "skip_commenters": ["Tracker AutoManager", "RHEL Jira Bot"]}})
        self.assertIn("[@Rafael Jeffman] Please review.", output)

    def test_jira_comment_uses_resolved_display_name(self):
        item = Item(
            "P", "jira", "", ["issues"], "issue", "RHEL-1", "url", "summary",
            groups=["triage"], comments=[{"email": "jira-bot@example.com", "display_name": "Jira Bot", "text": "Updated."}],
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["triage"]}})
        self.assertIn("[@Jira Bot] Updated.", output)

    def test_jira_comment_can_disable_mention_rendering(self):
        item = Item(
            "P", "jira", "", ["issues"], "issue", "RHEL-1", "url", "summary",
            groups=["triage"], comments=[{"email": "jira-bot@example.com", "display_name": "Jira Bot", "mention": False, "text": "Updated."}],
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["triage"]}})
        self.assertIn("[Jira Bot] Updated.", output)

    def test_jira_automation_comments_are_hidden(self):
        item = Item(
            "P", "jira", "", ["issues"], "issue", "RHEL-1", "url", "summary",
            groups=["triage"], comments=[
                {"display_name": "Tracker AutoManager", "text": "automated update"},
                {"author": "RHEL Jira bot", "text": "auto-close warning"},
                {"author": "Human User", "text": "human comment"},
            ],
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["triage"], "skip_commenters": ["Tracker AutoManager", "RHEL Jira Bot"]}})
        self.assertNotIn("automated update", output)
        self.assertNotIn("auto-close warning", output)
        self.assertIn("human comment", output)

    def test_jira_automation_authors_are_hidden_by_same_skip_list(self):
        item = Item(
            "P", "jira", "", ["issues"], "issue", "RHEL-1", "url", "summary",
            author="Tracker AutoManager", groups=["triage"],
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["triage"], "skip_commenters": ["Tracker AutoManager"]}})
        self.assertNotIn("**Author:**", output)

    def test_cached_provider_author_uses_mention_flag(self):
        item = Item(
            "P", "codeberg", "o/r", ["issues"], "issue", "1", "url", "summary",
            author="frenaud@redhat.com", groups=["triage"], metadata={"author_mention": True},
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["triage"]}})
        self.assertIn("**Author:** frenaud@redhat.com", output)

    def test_github_pull_request_url_uses_web_url(self):
        self.assertEqual(_github_item_url("o/r", 42, "pull_request"), "https://github.com/o/r/pull/42")

    def test_provider_normalizes_scalar_comments(self):
        item = _item(
            {"name": "P"},
            {"provider": "github", "repository": "o/r", "groups": [{"triage": {}}]},
            {"number": 1, "url": "url", "summary": "summary", "comments": 0},
            "issue",
        )
        self.assertEqual(item.comments, [])

    def test_group_filter_labels_are_hidden_from_report(self):
        item = _item(
            {"name": "P"},
            {"provider": "github", "repository": "o/r", "groups": [{"name": "needs_review", "filter": {"labels": ["needs review"]}}]},
            {"number": 1, "url": "url", "summary": "summary", "labels": ["needs review", "urgent"]},
            "pull_request",
            ["needs_review"],
        )
        output = render([item], {"report": {"title": "Custom", "groups": ["needs_review"]}})
        self.assertNotIn("needs review", output)
        self.assertIn("urgent", output)

    def test_source_groups_filter_items_independently(self):
        source = {"groups": [
            {"name": "issues", "filter": {"is": "issue"}},
            {"name": "review", "filter": {"is": "pr", "labels": ["needs review"]}},
        ]}
        self.assertEqual(_groups_for({"state": "open", "labels": [], "created_at": "2026-09-09T00:00:00Z"}, "issue", source), ["issues"])
        self.assertEqual(_groups_for({"state": "open", "labels": [{"name": "needs review"}], "created_at": "2026-09-09T00:00:00Z"}, "pull_request", source), ["review"])

    def test_source_group_excludes_matching_labels(self):
        source = {"groups": [{"name": "triage", "filter": {"exclude_labels": ["do-not-triage"]}}]}
        item = {"labels": [{"name": "Do-Not-Triage"}], "created_at": "2026-09-09T00:00:00Z"}
        self.assertEqual(_groups_for(item, "issue", source), [])

    def test_jira_cve_item_belongs_only_to_vulnerabilities(self):
        item = _item(
            {"name": "P"},
            {"provider": "jira", "groups": [{"triage": {}}]},
            {"key": "RHEL-1", "url": "url", "summary": "CVE", "cve_id": "CVE-2026-1"},
            "issue",
        )
        self.assertEqual(item.groups, ["vulnerabilities"])

    def test_user_map_cache_resolves_once_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.yml"
            lookups = []
            cache = UserCache(lambda email: lookups.append(email) or "Jira Bot", path)
            self.assertEqual(cache.resolve("jira", "jira-bot@example.com").name, "Jira Bot")
            self.assertEqual(cache.resolve("jira", "jira-bot@example.com").name, "Jira Bot")
            self.assertEqual(lookups, ["jira-bot@example.com"])
            self.assertIn("mention: true", path.read_text())
            self.assertEqual(UserCache(lambda email: None, path).resolve("jira", "jira-bot@example.com").name, "Jira Bot")

    def test_user_map_cache_skips_standard_emails_and_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            lookups = []
            cache = UserCache(lambda email: lookups.append(email), Path(directory) / "users.yml")
            self.assertEqual(cache.resolve("jira", "person@example.com", should_lookup=False).name, "person@example.com")
            self.assertEqual(cache.resolve("jira", "jira+bot@example.com").name, "jira+bot@example.com")
            self.assertEqual(lookups, ["jira+bot@example.com"])

    def test_user_cache_migrates_flat_jira_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.yml"
            path.write_text("users:\n  jira-bot@example.com: Jira Bot\n")
            cache = UserCache(lambda email: None, path)
            self.assertEqual(cache.resolve("jira", "jira-bot@example.com").name, "Jira Bot")

    def test_user_cache_keeps_provider_names_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.yml"
            cache = UserCache(lambda identifier: f"name-{identifier}", path)
            self.assertEqual(cache.resolve("github", "bot", True).name, "name-bot")
            self.assertEqual(cache.resolve("codeberg", "bot", True).name, "name-bot")
            content = path.read_text()
            self.assertIn("github:", content)
            self.assertIn("codeberg:", content)

    def test_mailto_redhat_links_render_as_mentions(self):
        comment = """
          href: \"mailto:person@example.com\"
          text: \"person@example.com\"
          href: \"mailto:someone@redhat.com\"
          text: \"someone@redhat.com\"
        """
        result = _parse_comment_text(comment)
        self.assertIn("person@example.com", result)
        self.assertIn("someone@redhat.com", result)
        self.assertNotIn("@person@example.com", result)

    def test_plain_redhat_email_renders_as_mention(self):
        result = _parse_comment_text('text: "on behalf of dcamilo@redhat.com created"')
        self.assertIn("dcamilo@redhat.com", result)

    def test_comment_email_mention_uses_known_user_name(self):
        from user_cache import CachedUser
        result = _parse_comment_text(
            'text: "on behalf of dcamilo@redhat.com created"',
            lambda email: CachedUser("Daniel Camilo"),
        )
        self.assertIn("@Daniel Camilo", result)
        self.assertNotIn("@dcamilo@redhat.com", result)


if __name__ == "__main__":
    unittest.main()
