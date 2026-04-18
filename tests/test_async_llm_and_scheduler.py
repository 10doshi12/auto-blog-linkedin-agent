import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

for key, value in {
    "GITHUB_TOKEN": "github-token",
    "GITHUB_USERNAME": "test-user",
    "OPENROUTER_API_KEY": "openrouter-token",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_KEY": "supabase-service-key",
}.items():
    os.environ.setdefault(key, value)

import index
from agent.core import database, llm
from agent.schemas.blog_post import BlogPostInsert
from agent.schemas.blog_post import _slugify
from agent.schemas.llm_output import LLMOutput
from agent.schemas.project import ProjectInsert
from postgrest import APIError


def make_llm_config(**overrides: object) -> SimpleNamespace:
    values = {
        "primary_model": "primary-model",
        "fallback_model": "fallback-model",
        "max_tokens": 8192,
        "temperature": 0.7,
        "max_concurrent_requests": 2,
        "connect_timeout_seconds": 10.0,
        "write_timeout_seconds": 10.0,
        "read_timeout_seconds": 120.0,
        "pool_timeout_seconds": 10.0,
        "max_retries": 2,
        "shutdown_grace_seconds": 0.1,
    }
    values.update(overrides)
    return SimpleNamespace(llm=SimpleNamespace(**values))


def make_openrouter_response(content: str, status_code: int = 200) -> httpx.Response:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return httpx.Response(
        status_code=status_code,
        request=request,
        json={"choices": [{"message": {"content": content}}]},
    )


def valid_llm_json() -> str:
    return """
    {
      "slug": "bounded-async-pipeline",
      "title": "Bounded Async Pipeline",
      "excerpt": "A concise summary of the project and why it matters.",
      "content": "This is a story-driven blog post with enough content to satisfy validation and exercise the async LLM path safely in tests.",
      "technical_content": "This is a technical deep dive with enough detail to pass validation while we test retries, cancellation, and fallback behavior.",
      "category": "ai-ml",
      "metric": "Cuts waiting time by 50%",
      "tags": ["python", "llm"],
      "linkedin_post": "This is a LinkedIn post body long enough to satisfy validation in the async tests."
    }
    """


class AsyncLLMTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_blog_content_retries_transport_error_on_same_model(self) -> None:
        client = Mock()
        client.post = AsyncMock(
            side_effect=[
                httpx.ReadTimeout("timed out"),
                make_openrouter_response(valid_llm_json()),
            ]
        )

        with patch.object(llm, "config", make_llm_config()):
            with patch("agent.core.llm.asyncio.sleep", AsyncMock()) as mock_sleep:
                result = await llm.generate_blog_content(client, readme="# README", repo_name="repo-a")

        self.assertIsInstance(result, LLMOutput)
        self.assertEqual(client.post.await_count, 2)
        models = [call.kwargs["json"]["model"] for call in client.post.await_args_list]
        self.assertEqual(models, ["primary-model", "primary-model"])
        mock_sleep.assert_awaited_once()

    async def test_generate_blog_content_falls_back_after_validation_failure(self) -> None:
        invalid_json = '{"title": "bad"}'
        client = Mock()
        client.post = AsyncMock(
            side_effect=[
                make_openrouter_response(invalid_json),
                make_openrouter_response(valid_llm_json()),
            ]
        )

        with patch.object(llm, "config", make_llm_config()):
            result = await llm.generate_blog_content(client, readme="# README", repo_name="repo-b")

        self.assertIsInstance(result, LLMOutput)
        models = [call.kwargs["json"]["model"] for call in client.post.await_args_list]
        self.assertEqual(models, ["primary-model", "fallback-model"])

    async def test_generate_blog_content_times_out_instead_of_hanging_indefinitely(self) -> None:
        client = Mock()
        client.post = AsyncMock(side_effect=httpx.ReadTimeout("still hanging"))

        with patch.object(llm, "config", make_llm_config(max_retries=1)):
            with patch("agent.core.llm.asyncio.sleep", AsyncMock()):
                with self.assertRaises(httpx.ReadTimeout):
                    await llm.generate_blog_content(client, readme="# README", repo_name="repo-c")

        self.assertEqual(client.post.await_count, 2)


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_all_limits_concurrency_to_two(self) -> None:
        controller = index.ShutdownController()
        controller.bind_loop(asyncio.get_running_loop())
        active = 0
        max_seen = 0

        async def fake_process(repo: dict, week_number: int | None, llm_client: object) -> None:
            del repo, week_number, llm_client
            nonlocal active, max_seen
            active += 1
            max_seen = max(max_seen, active)
            await asyncio.sleep(0.01)
            active -= 1

        repos = [{"repo_id": idx, "name": f"repo-{idx}"} for idx in range(5)]
        patched_config = make_llm_config(max_concurrent_requests=2, shutdown_grace_seconds=0.1)

        with patch.object(index, "config", patched_config):
            with patch.object(index, "_process_repo", side_effect=fake_process):
                await index._run_all(repos, 1, object(), controller)

        self.assertEqual(max_seen, 2)

    async def test_shutdown_cancels_in_flight_repo_tasks_without_starting_more(self) -> None:
        controller = index.ShutdownController()
        controller.bind_loop(asyncio.get_running_loop())
        started: list[str] = []
        cancelled: list[str] = []
        two_started = asyncio.Event()

        async def fake_process(repo: dict, week_number: int | None, llm_client: object) -> None:
            del week_number, llm_client
            started.append(repo["name"])
            if len(started) == 2:
                two_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(repo["name"])
                raise

        repos = [{"repo_id": idx, "name": f"repo-{idx}"} for idx in range(5)]
        patched_config = make_llm_config(max_concurrent_requests=2, shutdown_grace_seconds=0.1)

        with patch.object(index, "config", patched_config):
            with patch.object(index, "_process_repo", side_effect=fake_process):
                run_task = asyncio.create_task(index._run_all(repos, 1, object(), controller))
                await two_started.wait()
                controller.request_shutdown()
                await run_task

        self.assertEqual(len(started), 2)
        self.assertEqual(set(cancelled), set(started))


class DatabaseProcessingTests(unittest.TestCase):
    def test_cancelled_repos_are_not_considered_processed(self) -> None:
        execute = Mock(return_value=SimpleNamespace(data=[{"repo_id": 1}]))
        in_call = Mock(return_value=SimpleNamespace(execute=execute))
        select = Mock(return_value=SimpleNamespace(in_=in_call))
        fake_client = SimpleNamespace(table=Mock(return_value=SimpleNamespace(select=select)))

        with patch.object(database, "_client", fake_client):
            ids = database.get_processed_repo_ids()

        self.assertEqual(ids, {1})
        in_call.assert_called_once_with("status", ["success", "skipped"])

    def test_save_project_logs_postgrest_error_details(self) -> None:
        project = ProjectInsert.from_llm_output(
            source_repo_id=101,
            slug="dynamic-web-scraper",
            title="Dynamic Web Scraper Python",
            excerpt="Scrapes pages with a configurable workflow and stores useful output.",
            technical_content=(
                "Technical content with enough detail to exercise the project insert path "
                "without relying on the LLM in this unit test."
            ),
            category="full-stack",
            metric="Cuts repetitive scraping setup time by 20 minutes",
            tags=["python", "automation"],
            github_url="https://github.com/example/repo",
        )
        api_error = APIError(
            {
                "code": "23514",
                "message": 'new row for relation "projects" violates check constraint',
                "details": "Failing row contains (...)",
                "hint": "Inspect the projects table constraints",
            }
        )
        execute = Mock(side_effect=api_error)
        insert = Mock(return_value=SimpleNamespace(execute=execute))
        fake_client = SimpleNamespace(table=Mock(return_value=SimpleNamespace(insert=insert)))

        with patch.object(database, "_client", fake_client):
            with self.assertLogs("agent.core.database", level="ERROR") as captured_logs:
                with self.assertRaises(APIError):
                    database.save_project(project)

        self.assertTrue(
            any("code=23514" in message for message in captured_logs.output),
            captured_logs.output,
        )
        self.assertTrue(
            any("Failing row contains" in message for message in captured_logs.output),
            captured_logs.output,
        )


class ProjectSchemaTests(unittest.TestCase):
    def test_project_insert_defaults_has_detail_page_to_true(self) -> None:
        project = ProjectInsert.from_llm_output(
            source_repo_id=101,
            slug="dynamic-web-scraper",
            title="Dynamic Web Scraper Python",
            excerpt="Scrapes pages with a configurable workflow and stores useful output.",
            technical_content=(
                "Technical content with enough detail to build a valid project insert "
                "payload for this schema-focused unit test."
            ),
            category="full-stack",
            metric="Cuts repetitive scraping setup time by 20 minutes",
            tags=["python", "automation"],
            github_url="https://github.com/example/repo",
        )

        self.assertTrue(project.has_detail_page)
        self.assertEqual(project.category, "fullstack")

    def test_insert_builders_use_llm_slug_instead_of_slugifying_title(self) -> None:
        long_title = "Dynamic Web Scraper Python With A Title That Would Be Too Long For A Safe Slug"

        post = BlogPostInsert.from_llm_output(
            source_repo_id=101,
            slug="dynamic-web-scraper",
            title=long_title,
            excerpt="Scrapes pages with a configurable workflow and stores useful output.",
            content=(
                "This is a blog post body with enough content to create a valid insert payload "
                "while verifying that the provided LLM slug is used directly."
            ),
            tags=["python", "automation"],
            week_number=7,
        )
        project = ProjectInsert.from_llm_output(
            source_repo_id=101,
            slug="dynamic-web-scraper",
            title=long_title,
            excerpt="Scrapes pages with a configurable workflow and stores useful output.",
            technical_content=(
                "Technical content with enough detail to verify the project insert path uses "
                "the explicit LLM slug rather than deriving a long slug from the title."
            ),
            category="full-stack",
            metric="Cuts repetitive scraping setup time by 20 minutes",
            tags=["python", "automation"],
            github_url="https://github.com/example/repo",
        )

        self.assertEqual(post.slug, "dynamic-web-scraper")
        self.assertEqual(project.slug, "dynamic-web-scraper")
        self.assertEqual(project.category, "fullstack")
        self.assertNotEqual(post.slug, _slugify(long_title, max_length=30))


class LLMOutputSchemaTests(unittest.TestCase):
    def test_llm_output_normalizes_slug_to_safe_max_length(self) -> None:
        output = LLMOutput(
            slug="Dynamic Web Scraper Python For Long Titles In Production",
            title="Dynamic Web Scraper Python",
            excerpt="Scrapes pages with a configurable workflow and stores useful output.",
            content=(
                "This is a story-driven blog post with enough detail to satisfy validation "
                "while testing slug normalization behavior."
            ),
            technical_content=(
                "This is a technical deep dive with enough implementation detail to satisfy "
                "validation while testing slug normalization behavior."
            ),
            category="full-stack",
            metric="Cuts repetitive scraping setup time by 20 minutes",
            tags=["python", "automation"],
            linkedin_post="A ready to publish LinkedIn update with enough text to be valid.",
        )

        self.assertLessEqual(len(output.slug), 30)
        self.assertEqual(output.category, "fullstack")
        self.assertEqual(
            output.slug,
            _slugify("Dynamic Web Scraper Python For Long Titles In Production", max_length=30),
        )

    def test_llm_output_normalizes_legacy_full_stack_category(self) -> None:
        output = LLMOutput(
            slug="category-test",
            title="Category Test",
            excerpt="A short summary that is long enough to satisfy validation.",
            content=(
                "This is a story-driven blog post with enough detail to satisfy validation "
                "while testing category normalization."
            ),
            technical_content=(
                "This is a technical deep dive with enough implementation detail to satisfy "
                "validation while testing category normalization."
            ),
            category="full-stack",
            metric="Cuts repetitive setup time by 20 minutes",
            tags=["python", "automation"],
            linkedin_post="A ready to publish LinkedIn update with enough text to be valid.",
        )

        self.assertEqual(output.category, "fullstack")

    def test_llm_output_rejects_metric_longer_than_100_chars(self) -> None:
        with self.assertRaises(ValueError):
            LLMOutput(
                slug="metric-limit-test",
                title="Metric Limit Test",
                excerpt="A short summary that is long enough to satisfy validation.",
                content=(
                    "This is a story-driven blog post with enough detail to satisfy validation "
                    "while testing metric validation."
                ),
                technical_content=(
                    "This is a technical deep dive with enough implementation detail to satisfy "
                    "validation while testing metric validation."
                ),
                category="ai-ml",
                metric="x" * 101,
                tags=["python", "automation"],
                linkedin_post="A ready to publish LinkedIn update with enough text to be valid.",
            )

    def test_llm_output_normalizes_and_dedupes_tags(self) -> None:
        output = LLMOutput(
            slug="tag-normalization-test",
            title="Tag Normalization Test",
            excerpt="A short summary that is long enough to satisfy validation.",
            content=(
                "This is a story-driven blog post with enough detail to satisfy validation "
                "while testing tag normalization."
            ),
            technical_content=(
                "This is a technical deep dive with enough implementation detail to satisfy "
                "validation while testing tag normalization."
            ),
            category="ai-ml",
            metric="Cuts repetitive setup time by 20 minutes",
            tags=["Python", "web scraping", "python", "open_source"],
            linkedin_post="A ready to publish LinkedIn update with enough text to be valid.",
        )

        self.assertEqual(output.tags, ["python", "web-scraping", "open-source"])
