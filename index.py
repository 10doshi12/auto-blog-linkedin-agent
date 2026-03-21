import asyncio

from agent.config.agent_config import config
from agent.config.settings import DRY_RUN, GITHUB_USERNAME
from agent.core.database import (
    get_current_week,
    get_processed_repo_ids,
    mark_repo_processed,
    save_blog_post,
    save_project,
)
from agent.core.github import data_to_send_LLM
from agent.core.linkedin import post_to_linkedin
from agent.core.llm import check_openrouter_connection, generate_blog_content
from agent.schemas.blog_post import BlogPostInsert
from agent.schemas.project import ProjectInsert
from agent.utils.logger import get_logger

logger = get_logger(__name__)


async def _process_repo(repo_data: dict, week_number: int | None) -> None:
    repo_id: int = repo_data["repo_id"]
    repo_name: str = repo_data["name"]
    readme: str = repo_data["readme"]
    github_url: str = repo_data["repo_obj"]["html_url"]

    logger.info(f"[{repo_name}] Starting processing (repo_id={repo_id})")

    try:
        # --- LLM generation ---
        llm_output = await asyncio.to_thread(generate_blog_content, readme)
        logger.info(f"[{repo_name}] LLM content generated (title='{llm_output.title}')")

        # --- Build insert payloads ---
        post = BlogPostInsert.from_llm_output(
            title=llm_output.title,
            excerpt=llm_output.excerpt,
            content=llm_output.content,
            tags=llm_output.tags,
            week_number=week_number,
        )

        project = ProjectInsert.from_llm_output(
            title=llm_output.title,
            excerpt=llm_output.excerpt,
            technical_content=llm_output.technical_content,
            category=llm_output.category,
            metric=llm_output.metric,
            tags=llm_output.tags,
            github_url=github_url,
        )

        # --- Save to Supabase ---
        if DRY_RUN:
            logger.info(f"[{repo_name}] DRY RUN — skipping Supabase inserts")
            post_id = "dry-run"
        else:
            post_id = await asyncio.to_thread(save_blog_post, post)
            logger.info(f"[{repo_name}] Blog post saved (id={post_id})")

            project_id = await asyncio.to_thread(save_project, project)
            logger.info(f"[{repo_name}] Project saved (id={project_id})")

        # --- Post to LinkedIn (best-effort) ---
        linkedin_failed_content: str | None = None

        if DRY_RUN:
            logger.info(f"[{repo_name}] DRY RUN — skipping LinkedIn post")
        elif config.behaviour.disable_linkedin_posting:
            logger.info(f"[{repo_name}] CONFIG — skipping LinkedIn post (disable_linkedin_posting is True)")
            # Treat as "failed" to post, but without actual exception, or we could leave it as success
            # and record the content in raw_llm_output so it isn't lost. 
            # Actually we shouldn't mark it as failed unless it really failed. 
            # We'll just park the content in raw_llm_output for reference or manual posting.
            linkedin_failed_content = llm_output.linkedin_post
        else:
            try:
                post_urn = await asyncio.to_thread(post_to_linkedin, llm_output.linkedin_post, github_url)
                logger.info(f"[{repo_name}] LinkedIn post published (urn={post_urn})")
            except Exception as linkedin_err:
                logger.error(
                    f"[{repo_name}] LinkedIn posting failed (blog post still saved): {linkedin_err}"
                )
                linkedin_failed_content = llm_output.linkedin_post

        # --- Mark as success ---
        await asyncio.to_thread(
            mark_repo_processed,
            repo_id=repo_id,
            repo_name=repo_name,
            status="success",
            blog_post_id=post_id if post_id != "dry-run" else None,
            raw_llm_output={"linkedin_post": linkedin_failed_content} if linkedin_failed_content else None,
        )
        logger.info(f"[{repo_name}] Marked as success")

    except Exception as e:
        logger.error(f"[{repo_name}] Processing failed: {e}", exc_info=True)
        await asyncio.to_thread(
            mark_repo_processed,
            repo_id=repo_id,
            repo_name=repo_name,
            status="failed",
            raw_llm_output=None,
        )


async def _run_all(repos: list[dict], week_number: int | None) -> None:
    await asyncio.gather(*[
        _process_repo(repo, week_number) for repo in repos
    ])


def main() -> None:
    logger.info("=== Auto Blog Agent starting ===")

    # 1. Check OpenRouter connectivity
    if not check_openrouter_connection():
        logger.error("OpenRouter connection check failed — aborting")
        return
    logger.info("OpenRouter connection OK")

    # 2. Get current week from site_config
    week_number = get_current_week()
    if week_number is None:
        logger.warning("current_week unset in site_config — blog posts will have week_number=None")

    # 3. Fetch repos created this IST week
    repos = data_to_send_LLM(owner=GITHUB_USERNAME)
    if not repos:
        logger.info("No new repos found this week — nothing to process")
        return

    # 4. Filter already-processed repos
    processed_ids = get_processed_repo_ids()
    repos_to_process = [r for r in repos if r["repo_id"] not in processed_ids]

    skipped = len(repos) - len(repos_to_process)
    if skipped:
        logger.info(f"Skipping {skipped} already-processed repo(s)")

    if not repos_to_process:
        logger.info("All repos already processed — nothing to do")
        return

    logger.info(f"Processing {len(repos_to_process)} repo(s) async")

    # 5. Run per-repo pipeline concurrently
    asyncio.run(_run_all(repos_to_process, week_number))

    logger.info("=== Auto Blog Agent finished ===")


if __name__ == "__main__":
    main()