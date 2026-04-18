import asyncio
import signal

import httpx

from agent.config.agent_config import config
from agent.config.settings import DRY_RUN, GITHUB_USERNAME
from agent.core.database import (
    get_current_week,
    get_processed_repo_ids,
    mark_repo_in_progress,
    persist_repo_result,
)
from agent.core.github import data_to_send_LLM
from agent.core.linkedin import LinkedInConfigError, post_to_linkedin
from agent.core.llm import (
    check_openrouter_connection,
    create_openrouter_client,
    generate_blog_content,
)
from agent.schemas.blog_post import BlogPostInsert
from agent.schemas.llm_output import LLMOutput
from agent.schemas.project import ProjectInsert
from agent.utils.logger import get_logger

logger = get_logger(__name__)


class ShutdownController:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._active_tasks: set[asyncio.Task] = set()
        self._interrupt_count = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._shutdown_event = asyncio.Event()

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_event is not None and self._shutdown_event.is_set()

    @property
    def active_tasks(self) -> set[asyncio.Task]:
        return set(self._active_tasks)

    def track_task(self, task: asyncio.Task) -> None:
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    def request_shutdown(self) -> bool:
        self._interrupt_count += 1

        if self._interrupt_count == 1:
            logger.warning(
                "Interrupt received, stopping new work and cancelling %s in-flight repo task(s)",
                len(self._active_tasks),
            )

            if self._loop is not None:
                try:
                    running_loop = asyncio.get_running_loop()
                except RuntimeError:
                    running_loop = None

                if running_loop is self._loop:
                    self._begin_shutdown()
                else:
                    self._loop.call_soon_threadsafe(self._begin_shutdown)
            return False

        logger.error("Second interrupt received, aborting immediately")
        return True

    def _begin_shutdown(self) -> None:
        if self._shutdown_event is not None:
            self._shutdown_event.set()

        for task in list(self._active_tasks):
            task.cancel()


def _blog_post_id_or_none(post_id: str | None) -> str | None:
    if post_id in (None, "dry-run"):
        return None
    return post_id


def _build_raw_llm_output(
    llm_output: LLMOutput | None,
    *,
    linkedin_status: str,
) -> dict | None:
    if llm_output is None:
        return None

    payload = llm_output.model_dump()
    payload["linkedin_status"] = linkedin_status
    return payload


async def _persist_processed_repo(
    *,
    repo_id: int,
    repo_name: str,
    status: str,
    blog_post: BlogPostInsert | None = None,
    project: ProjectInsert | None = None,
    skip_reason: str | None = None,
    raw_llm_output: dict | None = None,
) -> tuple[str | None, str | None]:
    outcome = await asyncio.to_thread(
        persist_repo_result,
        repo_id=repo_id,
        repo_name=repo_name,
        status=status,
        blog_post=blog_post,
        project=project,
        skip_reason=skip_reason,
        raw_llm_output=raw_llm_output,
    )
    return outcome.blog_post_id, outcome.project_id


async def _process_repo(
    repo_data: dict,
    week_number: int | None,
    llm_client: httpx.AsyncClient,
) -> None:
    repo_id: int = repo_data["repo_id"]
    repo_name: str = repo_data["name"]
    readme: str = repo_data["readme"]
    github_url: str = repo_data["repo_obj"]["html_url"]
    llm_output: LLMOutput | None = None
    post: BlogPostInsert | None = None
    project: ProjectInsert | None = None
    post_id: str | None = None
    project_id: str | None = None
    linkedin_status = "not_attempted"

    logger.info("[%s] Starting processing (repo_id=%s)", repo_name, repo_id)

    try:
        if not DRY_RUN:
            await asyncio.to_thread(mark_repo_in_progress, repo_id=repo_id, repo_name=repo_name)
            logger.info("[%s] Marked as in_progress", repo_name)

        llm_output = await generate_blog_content(
            llm_client,
            readme=readme,
            repo_name=repo_name,
        )
        logger.info("[%s] LLM content generated (title='%s')", repo_name, llm_output.title)

        post = BlogPostInsert.from_llm_output(
            source_repo_id=repo_id,
            slug=llm_output.slug,
            title=llm_output.title,
            excerpt=llm_output.excerpt,
            content=llm_output.content,
            tags=llm_output.tags,
            week_number=week_number,
        )

        project = ProjectInsert.from_llm_output(
            source_repo_id=repo_id,
            slug=llm_output.slug,
            title=llm_output.title,
            excerpt=llm_output.excerpt,
            technical_content=llm_output.technical_content,
            category=llm_output.category,
            metric=llm_output.metric,
            tags=llm_output.tags,
            github_url=github_url,
        )

        if DRY_RUN:
            logger.info("[%s] DRY RUN — skipping Supabase inserts", repo_name)
            post_id = "dry-run"
        else:
            logger.info("[%s] Prepared transactional blog/project payloads", repo_name)

        if DRY_RUN:
            logger.info("[%s] DRY RUN — skipping LinkedIn post", repo_name)
            linkedin_status = "dry_run"
        elif config.behaviour.disable_linkedin_posting:
            logger.info(
                "[%s] CONFIG — skipping LinkedIn post (disable_linkedin_posting is True)",
                repo_name,
            )
            linkedin_status = "disabled"
        else:
            try:
                post_urn = await asyncio.to_thread(post_to_linkedin, llm_output.linkedin_post, github_url)
                logger.info("[%s] LinkedIn post published (urn=%s)", repo_name, post_urn)
                linkedin_status = "published"
            except LinkedInConfigError as linkedin_config_err:
                logger.warning(
                    "[%s] LinkedIn config missing, skipping publish: %s",
                    repo_name,
                    linkedin_config_err,
                )
                linkedin_status = "missing_config"
            except Exception as linkedin_err:
                logger.error(
                    "[%s] LinkedIn posting failed (blog post still saved): %s",
                    repo_name,
                    linkedin_err,
                )
                linkedin_status = "publish_failed"

        post_id, project_id = await _persist_processed_repo(
            repo_id=repo_id,
            repo_name=repo_name,
            status="success",
            blog_post=None if DRY_RUN else post,
            project=None if DRY_RUN else project,
            raw_llm_output=_build_raw_llm_output(llm_output, linkedin_status=linkedin_status),
        )
        logger.info(
            "[%s] Marked as success (blog_post_id=%s, project_id=%s)",
            repo_name,
            _blog_post_id_or_none(post_id),
            project_id,
        )

    except asyncio.CancelledError:
        logger.warning("[%s] Processing cancelled", repo_name)
        try:
            post_id, project_id = await asyncio.shield(
                _persist_processed_repo(
                    repo_id=repo_id,
                    repo_name=repo_name,
                    status="cancelled",
                    raw_llm_output=_build_raw_llm_output(
                        llm_output,
                        linkedin_status=linkedin_status,
                    ),
                )
            )
            logger.info(
                "[%s] Marked as cancelled (blog_post_id=%s, project_id=%s)",
                repo_name,
                _blog_post_id_or_none(post_id),
                project_id,
            )
        except Exception as cancel_err:
            logger.error("[%s] Failed to persist cancelled state: %s", repo_name, cancel_err)
        raise
    except Exception as exc:
        logger.error("[%s] Processing failed: %s", repo_name, exc, exc_info=True)
        post_id, project_id = await _persist_processed_repo(
            repo_id=repo_id,
            repo_name=repo_name,
            status="failed",
            raw_llm_output=_build_raw_llm_output(
                llm_output,
                linkedin_status=linkedin_status,
            ),
        )
        logger.info(
            "[%s] Marked as failed (blog_post_id=%s, project_id=%s)",
            repo_name,
            _blog_post_id_or_none(post_id),
            project_id,
        )


def _drain_completed_tasks(done_tasks: set[asyncio.Task]) -> None:
    for task in done_tasks:
        try:
            task.result()
        except asyncio.CancelledError:
            continue
        except Exception as exc:
            logger.error("Unexpected repo task failure: %s", exc, exc_info=True)


async def _await_shutdown_tasks(tasks: set[asyncio.Task]) -> None:
    if not tasks:
        return

    done, pending = await asyncio.wait(
        tasks,
        timeout=config.llm.shutdown_grace_seconds,
    )
    _drain_completed_tasks(done)

    if pending:
        logger.warning(
            "Shutdown grace period expired with %s repo task(s) still pending",
            len(pending),
        )


async def _run_all(
    repos: list[dict],
    week_number: int | None,
    llm_client: httpx.AsyncClient,
    shutdown_controller: ShutdownController,
) -> None:
    max_parallel = max(1, config.llm.max_concurrent_requests)
    repo_index = 0
    active_tasks: set[asyncio.Task] = set()

    while repo_index < len(repos) or active_tasks:
        while (
            repo_index < len(repos)
            and len(active_tasks) < max_parallel
            and not shutdown_controller.shutdown_requested
        ):
            task = asyncio.create_task(
                _process_repo(repos[repo_index], week_number, llm_client)
            )
            shutdown_controller.track_task(task)
            active_tasks.add(task)
            repo_index += 1

        if shutdown_controller.shutdown_requested:
            await _await_shutdown_tasks(active_tasks)
            return

        if not active_tasks:
            break

        done, pending = await asyncio.wait(
            active_tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )
        _drain_completed_tasks(done)
        active_tasks = pending


async def async_main(shutdown_controller: ShutdownController) -> None:
    logger.info("=== Auto Blog Agent starting ===")

    async with create_openrouter_client() as llm_client:
        if not await check_openrouter_connection(llm_client):
            logger.error("OpenRouter connection check failed — aborting")
            return
        logger.info("OpenRouter connection OK")

        if shutdown_controller.shutdown_requested:
            logger.warning("Shutdown requested before repo processing started")
            return

        week_number = await asyncio.to_thread(get_current_week)
        if week_number is None:
            logger.warning("current_week unset in site_config — blog posts will have week_number=None")

        if shutdown_controller.shutdown_requested:
            logger.warning("Shutdown requested before GitHub fetch started")
            return

        repos = await asyncio.to_thread(data_to_send_LLM, owner=GITHUB_USERNAME)
        if not repos:
            logger.info("No new repos found this week — nothing to process")
            return

        processed_ids = await asyncio.to_thread(get_processed_repo_ids)
        repos_to_process = [repo for repo in repos if repo["repo_id"] not in processed_ids]

        skipped = len(repos) - len(repos_to_process)
        if skipped:
            logger.info("Skipping %s already-processed repo(s)", skipped)

        if not repos_to_process:
            logger.info("All repos already processed — nothing to do")
            return

        logger.info(
            "Processing %s repo(s) with max OpenRouter concurrency=%s",
            len(repos_to_process),
            config.llm.max_concurrent_requests,
        )

        await _run_all(repos_to_process, week_number, llm_client, shutdown_controller)

    if shutdown_controller.shutdown_requested:
        logger.warning("=== Auto Blog Agent stopped after shutdown request ===")
    else:
        logger.info("=== Auto Blog Agent finished ===")


def main() -> None:
    shutdown_controller = ShutdownController()
    previous_sigint = signal.getsignal(signal.SIGINT)

    def _handle_sigint(signum: int, frame: object | None) -> None:
        del signum, frame
        if shutdown_controller.request_shutdown():
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handle_sigint)

    try:
        with asyncio.Runner() as runner:
            shutdown_controller.bind_loop(runner.get_loop())
            runner.run(async_main(shutdown_controller))
    except KeyboardInterrupt:
        logger.error("Forced shutdown requested — exiting immediately")
    finally:
        signal.signal(signal.SIGINT, previous_sigint)


if __name__ == "__main__":
    main()
