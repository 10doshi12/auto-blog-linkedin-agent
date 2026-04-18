import asyncio
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

for key, value in {
    "GITHUB_TOKEN": "github-token",
    "GITHUB_USERNAME": "test-user",
    "OPENROUTER_API_KEY": "openrouter-token",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_KEY": "supabase-service-key",
}.items():
    os.environ.setdefault(key, value)

import index
from agent.core.linkedin import LinkedInConfigError, post_to_linkedin
from agent.schemas.llm_output import LLMOutput


class LinkedInPublisherTests(unittest.TestCase):
    def test_post_to_linkedin_raises_before_http_when_access_token_missing(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LINKEDIN_ACCESS_TOKEN": "",
                "LINKEDIN_PERSON_URN": "urn:li:person:abc123",
            },
            clear=False,
        ):
            with patch("agent.core.linkedin.httpx.post") as mock_post:
                with self.assertRaises(LinkedInConfigError) as ctx:
                    post_to_linkedin("Hello LinkedIn")

        self.assertEqual(ctx.exception.missing_fields, ["LINKEDIN_ACCESS_TOKEN"])
        mock_post.assert_not_called()

    def test_post_to_linkedin_raises_before_http_when_person_urn_missing(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LINKEDIN_ACCESS_TOKEN": "li-token",
                "LINKEDIN_PERSON_URN": "",
            },
            clear=False,
        ):
            with patch("agent.core.linkedin.httpx.post") as mock_post:
                with self.assertRaises(LinkedInConfigError) as ctx:
                    post_to_linkedin("Hello LinkedIn")

        self.assertEqual(ctx.exception.missing_fields, ["LINKEDIN_PERSON_URN"])
        mock_post.assert_not_called()

    def test_post_to_linkedin_builds_headers_and_payload_at_call_time(self) -> None:
        response = Mock()
        response.headers = {"X-RestLi-Id": "urn:li:share:123"}
        response.raise_for_status = Mock()

        with patch.dict(
            os.environ,
            {
                "LINKEDIN_ACCESS_TOKEN": "li-token",
                "LINKEDIN_PERSON_URN": "urn:li:person:abc123",
            },
            clear=False,
        ):
            with patch("agent.core.linkedin.httpx.post", return_value=response) as mock_post:
                post_urn = post_to_linkedin("Shipping this week", "https://github.com/example/repo")

        self.assertEqual(post_urn, "urn:li:share:123")
        mock_post.assert_called_once()

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer li-token")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(kwargs["headers"]["X-Restli-Protocol-Version"], "2.0.0")
        self.assertEqual(kwargs["json"]["author"], "urn:li:person:abc123")
        self.assertEqual(
            kwargs["json"]["specificContent"]["com.linkedin.ugc.ShareContent"]["shareCommentary"]["text"],
            "Shipping this week\n\nGitHub Repo: https://github.com/example/repo",
        )

    def test_post_to_linkedin_truncates_text_after_adding_github_url(self) -> None:
        response = Mock()
        response.headers = {"X-RestLi-Id": "urn:li:share:456"}
        response.raise_for_status = Mock()
        patched_config = SimpleNamespace(content=SimpleNamespace(linkedin_post_max_length=60))

        with patch.dict(
            os.environ,
            {
                "LINKEDIN_ACCESS_TOKEN": "li-token",
                "LINKEDIN_PERSON_URN": "urn:li:person:abc123",
            },
            clear=False,
        ):
            with patch.object(index, "config", patched_config):
                with patch("agent.core.linkedin.config", patched_config):
                    with patch("agent.core.linkedin.httpx.post", return_value=response) as mock_post:
                        post_to_linkedin(
                            "This LinkedIn body is intentionally longer than the configured limit.",
                            "https://github.com/example/repo",
                        )

        posted_text = mock_post.call_args.kwargs["json"]["specificContent"]["com.linkedin.ugc.ShareContent"]["shareCommentary"]["text"]
        self.assertLessEqual(len(posted_text), 60)
        self.assertTrue(posted_text.endswith("GitHub Repo: https://github.com/example/repo"))


class ProcessRepoLinkedInTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repo_data = {
            "repo_id": 42,
            "name": "week-one-agent",
            "readme": "# README",
            "repo_obj": {"html_url": "https://github.com/example/week-one-agent"},
        }
        self.llm_output = LLMOutput(
            slug="week-one-agent",
            title="Week One Agent",
            excerpt="A short summary for the post.",
            content=(
                "This is a story-driven blog post content with enough detail to pass validation, "
                "cover the project journey, explain the outcome, and leave enough room for the "
                "pipeline tests to focus on LinkedIn behavior instead of schema errors."
            ),
            technical_content=(
                "This is a technical write-up with enough implementation detail to pass validation, "
                "describe the architecture, mention the data flow, and make the test fixture look "
                "like a real LLM response instead of a tiny placeholder paragraph."
            ),
            category="ai-ml",
            metric="Cut manual posting time by 80%",
            tags=["python", "automation"],
            linkedin_post="A ready to publish LinkedIn update with enough text to be valid.",
        )
        self.llm_client = object()

    def expected_raw_llm_output(self, linkedin_status: str) -> dict:
        payload = self.llm_output.model_dump()
        payload["linkedin_status"] = linkedin_status
        return payload

    async def test_process_repo_marks_missing_linkedin_config_as_success_and_stores_recovery_data(self) -> None:
        patched_config = SimpleNamespace(
            behaviour=SimpleNamespace(disable_linkedin_posting=False)
        )

        with patch.dict(
            os.environ,
            {
                "LINKEDIN_ACCESS_TOKEN": "",
                "LINKEDIN_PERSON_URN": "",
            },
            clear=False,
        ):
            with patch("agent.core.linkedin.httpx.post") as mock_http_post:
                with patch.object(index, "DRY_RUN", False):
                    with patch.object(index, "config", patched_config):
                        with patch.object(index, "mark_repo_in_progress", return_value=SimpleNamespace()):
                            with patch.object(
                                index,
                                "persist_repo_result",
                                return_value=SimpleNamespace(blog_post_id="post-123", project_id="project-123"),
                            ) as mock_persist:
                                with patch.object(
                                    index,
                                    "generate_blog_content",
                                    AsyncMock(return_value=self.llm_output),
                                ):
                                    await index._process_repo(self.repo_data, 7, self.llm_client)

        mock_http_post.assert_not_called()
        kwargs = mock_persist.call_args.kwargs
        self.assertEqual(kwargs["status"], "success")
        self.assertEqual(
            kwargs["raw_llm_output"],
            self.expected_raw_llm_output("missing_config"),
        )

    async def test_process_repo_skips_disabled_linkedin_without_calling_publisher(self) -> None:
        patched_config = SimpleNamespace(
            behaviour=SimpleNamespace(disable_linkedin_posting=True)
        )

        with patch.object(index, "DRY_RUN", False):
            with patch.object(index, "config", patched_config):
                with patch.object(index, "mark_repo_in_progress", return_value=SimpleNamespace()):
                    with patch.object(
                        index,
                        "persist_repo_result",
                        return_value=SimpleNamespace(blog_post_id="post-123", project_id="project-123"),
                    ) as mock_persist:
                        with patch.object(
                            index,
                            "generate_blog_content",
                            AsyncMock(return_value=self.llm_output),
                        ):
                            with patch.object(index, "post_to_linkedin") as mock_post_to_linkedin:
                                await index._process_repo(self.repo_data, 7, self.llm_client)

        mock_post_to_linkedin.assert_not_called()
        self.assertEqual(
            mock_persist.call_args.kwargs["raw_llm_output"],
            self.expected_raw_llm_output("disabled"),
        )

    async def test_process_repo_dry_run_skips_linkedin_without_http_calls(self) -> None:
        patched_config = SimpleNamespace(
            behaviour=SimpleNamespace(disable_linkedin_posting=False)
        )

        with patch.object(index, "DRY_RUN", True):
            with patch.object(index, "config", patched_config):
                with patch.object(index, "mark_repo_in_progress") as mock_mark_in_progress:
                    with patch.object(
                        index,
                        "persist_repo_result",
                        return_value=SimpleNamespace(blog_post_id=None, project_id=None),
                    ) as mock_persist:
                        with patch.object(
                            index,
                            "generate_blog_content",
                            AsyncMock(return_value=self.llm_output),
                        ):
                            with patch.object(index, "post_to_linkedin") as mock_post_to_linkedin:
                                await index._process_repo(self.repo_data, 7, self.llm_client)

        mock_mark_in_progress.assert_not_called()
        mock_post_to_linkedin.assert_not_called()
        self.assertEqual(
            mock_persist.call_args.kwargs["raw_llm_output"],
            self.expected_raw_llm_output("dry_run"),
        )

    async def test_process_repo_failed_after_generation_still_stores_llm_output(self) -> None:
        patched_config = SimpleNamespace(
            behaviour=SimpleNamespace(disable_linkedin_posting=True)
        )

        with patch.object(index, "DRY_RUN", False):
            with patch.object(index, "config", patched_config):
                with patch.object(index, "mark_repo_in_progress", return_value=SimpleNamespace()):
                    with patch.object(
                        index,
                        "persist_repo_result",
                        side_effect=[
                            RuntimeError("db down"),
                            SimpleNamespace(blog_post_id=None, project_id=None),
                        ],
                    ) as mock_persist:
                        with patch.object(
                            index,
                            "generate_blog_content",
                            AsyncMock(return_value=self.llm_output),
                        ):
                            await index._process_repo(self.repo_data, 7, self.llm_client)

        kwargs = mock_persist.call_args.kwargs
        self.assertEqual(kwargs["status"], "failed")
        self.assertEqual(
            kwargs["raw_llm_output"],
            self.expected_raw_llm_output("disabled"),
        )

    async def test_process_repo_cancelled_after_llm_output_marks_repo_cancelled(self) -> None:
        patched_config = SimpleNamespace(
            behaviour=SimpleNamespace(disable_linkedin_posting=True)
        )
        save_started = threading.Event()

        def persist_with_slow_success(**kwargs: object) -> SimpleNamespace:
            if kwargs["status"] == "success":
                save_started.set()
                time.sleep(0.2)
                return SimpleNamespace(blog_post_id="post-123", project_id="project-123")
            return SimpleNamespace(blog_post_id=None, project_id=None)

        with patch.object(index, "DRY_RUN", False):
            with patch.object(index, "config", patched_config):
                with patch.object(index, "mark_repo_in_progress", return_value=SimpleNamespace()):
                    with patch.object(
                        index,
                        "persist_repo_result",
                        side_effect=persist_with_slow_success,
                    ) as mock_persist:
                        with patch.object(
                            index,
                            "generate_blog_content",
                            AsyncMock(return_value=self.llm_output),
                        ):
                            task = asyncio.create_task(index._process_repo(self.repo_data, 7, self.llm_client))
                            await asyncio.to_thread(save_started.wait, 1.0)
                            task.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await task

        kwargs = mock_persist.call_args.kwargs
        self.assertEqual(kwargs["status"], "cancelled")
        self.assertEqual(
            kwargs["raw_llm_output"],
            self.expected_raw_llm_output("disabled"),
        )

    async def test_process_repo_cancelled_before_llm_output_marks_repo_cancelled_without_payload(self) -> None:
        patched_config = SimpleNamespace(
            behaviour=SimpleNamespace(disable_linkedin_posting=False)
        )
        started = asyncio.Event()

        async def hanging_generate_blog_content(*args: object, **kwargs: object) -> LLMOutput:
            del args, kwargs
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        with patch.object(index, "DRY_RUN", False):
            with patch.object(index, "config", patched_config):
                with patch.object(index, "mark_repo_in_progress", return_value=SimpleNamespace()):
                    with patch.object(index, "generate_blog_content", side_effect=hanging_generate_blog_content):
                        with patch.object(
                            index,
                            "persist_repo_result",
                            return_value=SimpleNamespace(blog_post_id=None, project_id=None),
                        ) as mock_persist:
                            task = asyncio.create_task(index._process_repo(self.repo_data, 7, self.llm_client))
                            await started.wait()
                            task.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await task

        kwargs = mock_persist.call_args.kwargs
        self.assertEqual(kwargs["status"], "cancelled")
        self.assertIsNone(kwargs["raw_llm_output"])
