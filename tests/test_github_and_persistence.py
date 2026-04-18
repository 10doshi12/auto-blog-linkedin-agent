import base64
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

for key, value in {
    "GITHUB_TOKEN": "github-token",
    "GITHUB_USERNAME": "test-user",
    "OPENROUTER_API_KEY": "openrouter-token",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_KEY": "supabase-service-key",
}.items():
    os.environ.setdefault(key, value)

from agent.core import database, github
from agent.schemas.blog_post import BlogPostInsert
from agent.schemas.project import ProjectInsert


def make_github_config(**overrides: object) -> SimpleNamespace:
    github_values = {
        "max_repos_per_run": 5,
        "max_readme_length": 20000,
        "skip_forked_repos": True,
        "per_page": 100,
        "connect_timeout_seconds": 10.0,
        "write_timeout_seconds": 10.0,
        "read_timeout_seconds": 30.0,
        "pool_timeout_seconds": 10.0,
        "max_retries": 1,
    }
    github_values.update(overrides)
    return SimpleNamespace(
        github=SimpleNamespace(**github_values),
        behaviour=SimpleNamespace(skip_repos_without_readme=True),
    )


def make_response(
    *,
    json_data: object,
    status_code: int = 200,
    links: dict[str, dict[str, str]] | None = None,
) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.json.return_value = json_data
    response.raise_for_status = Mock()
    response.links = links or {}
    return response


class DummyClientContext:
    def __init__(self, client: object) -> None:
        self.client = client

    def __enter__(self) -> object:
        return self.client

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        del exc_type, exc, tb
        return False


class GitHubDiscoveryTests(unittest.TestCase):
    def test_data_to_send_llm_uses_testing_override_repo_names(self) -> None:
        with patch.object(github, "_TEST_REPO_NAMES", ("repo-one", "repo-two")):
            with patch.object(github, "config", make_github_config(max_repos_per_run=1)):
                with patch.object(
                    github,
                    "manual_repo_fetch",
                    side_effect=[
                        {"name": "repo-one", "repo_id": 1, "repo_obj": {}, "readme": "# repo one"},
                        {"name": "repo-two", "repo_id": 2, "repo_obj": {}, "readme": "# repo two"},
                    ],
                ) as mock_manual_fetch:
                    repos = github.data_to_send_LLM("test-user")

        self.assertEqual([repo["name"] for repo in repos], ["repo-one"])
        mock_manual_fetch.assert_called_once_with(repo_name="repo-one", owner="test-user")

    def test_data_to_send_llm_paginates_and_stops_after_week_boundary(self) -> None:
        week_start = datetime(2026, 3, 30, 0, 0, tzinfo=github.IST)
        week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)

        page_one = make_response(
            json_data=[
                {
                    "id": 1,
                    "name": "repo-one",
                    "created_at": "2026-03-30T01:00:00Z",
                    "html_url": "https://github.com/example/repo-one",
                    "fork": False,
                }
            ],
            links={"next": {"url": "https://api.github.com/users/test-user/repos?page=2"}},
        )
        page_two = make_response(
            json_data=[
                {
                    "id": 2,
                    "name": "repo-two",
                    "created_at": "2026-03-31T01:00:00Z",
                    "html_url": "https://github.com/example/repo-two",
                    "fork": False,
                },
                {
                    "id": 3,
                    "name": "repo-old",
                    "created_at": "2026-03-20T01:00:00Z",
                    "html_url": "https://github.com/example/repo-old",
                    "fork": False,
                },
            ],
        )
        fake_client = SimpleNamespace(get=Mock(side_effect=[page_one, page_two]))

        with patch.object(github, "config", make_github_config()):
            with patch.object(github, "get_current_week_range_ist", return_value=(week_start, week_end)):
                with patch.object(github, "create_github_client", return_value=DummyClientContext(fake_client)):
                    with patch.object(github, "get_readme_content", side_effect=["# repo one", "# repo two"]):
                        repos = github.data_to_send_LLM("test-user")

        self.assertEqual([repo["name"] for repo in repos], ["repo-one", "repo-two"])
        self.assertEqual(fake_client.get.call_count, 2)

    def test_get_readme_content_truncates_to_config_limit(self) -> None:
        encoded_content = base64.b64encode(b"x" * 20).decode("ascii")
        response = make_response(json_data={"content": encoded_content})

        with patch.object(github, "config", make_github_config(max_readme_length=10)):
            with patch.object(github, "_request_json", return_value=response):
                content = github.get_readme_content(Mock(), "test-user", "repo-one")

        self.assertEqual(content, "x" * 10)

    def test_get_readme_content_returns_none_on_404(self) -> None:
        response = make_response(json_data={}, status_code=404)

        with patch.object(github, "_request_json", return_value=response):
            content = github.get_readme_content(Mock(), "test-user", "repo-one")

        self.assertIsNone(content)

    def test_request_json_retries_transport_errors(self) -> None:
        response = make_response(json_data={"ok": True})
        client = Mock()
        client.get.side_effect = [httpx.ReadTimeout("timed out"), response]

        with patch.object(github, "config", make_github_config(max_retries=1)):
            with patch("agent.core.github.time.sleep") as mock_sleep:
                returned = github._request_json(client, "https://api.github.com/example")

        self.assertIs(returned, response)
        self.assertEqual(client.get.call_count, 2)
        mock_sleep.assert_called_once()


class PersistenceTests(unittest.TestCase):
    def test_persist_repo_result_calls_rpc_and_returns_ids(self) -> None:
        post = BlogPostInsert.from_llm_output(
            source_repo_id=42,
            slug="repo-forty-two",
            title="Repo Forty Two",
            excerpt="A short summary that is long enough to satisfy validation.",
            content=(
                "This is a blog post body with enough content to satisfy validation "
                "while testing RPC persistence."
            ),
            tags=["python", "automation"],
            week_number=7,
        )
        project = ProjectInsert.from_llm_output(
            source_repo_id=42,
            slug="repo-forty-two",
            title="Repo Forty Two",
            excerpt="A short summary that is long enough to satisfy validation.",
            technical_content=(
                "This is a technical deep dive with enough implementation detail to satisfy "
                "validation while testing RPC persistence."
            ),
            category="fullstack",
            metric="Cuts repetitive setup time by 20 minutes",
            tags=["python", "automation"],
            github_url="https://github.com/example/repo-forty-two",
        )
        execute = Mock(return_value=SimpleNamespace(data={"blog_post_id": "post-123", "project_id": "project-456"}))
        rpc = Mock(return_value=SimpleNamespace(execute=execute))
        fake_client = SimpleNamespace(rpc=rpc)

        with patch.object(database, "_client", fake_client):
            outcome = database.persist_repo_result(
                repo_id=42,
                repo_name="repo-forty-two",
                status="success",
                blog_post=post,
                project=project,
                raw_llm_output={"title": "Repo Forty Two"},
            )

        self.assertEqual(outcome.blog_post_id, "post-123")
        self.assertEqual(outcome.project_id, "project-456")
        rpc.assert_called_once()
        self.assertEqual(rpc.call_args.args[0], "persist_repo_result")
        params = rpc.call_args.args[1]
        self.assertEqual(params["p_repo_id"], 42)
        self.assertEqual(params["p_blog_post"]["source_repo_id"], 42)
        self.assertEqual(params["p_project"]["source_repo_id"], 42)
