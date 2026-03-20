from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from supabase import create_client, Client
from agent.config.settings import SUPABASE_URL, SUPABASE_SERVICE_KEY
from agent.schemas.blog_post import BlogPostInsert
from agent.schemas.project import ProjectInsert
from agent.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Table names
# ---------------------------------------------------------------------------

_BLOG_POSTS_TABLE = "posts"
_PROJECTS_TABLE = "projects"
_PROCESSED_REPOS_TABLE = "agent_processed_repos"

# ---------------------------------------------------------------------------
# Client (module-level singleton — initialised once on first import)
# ---------------------------------------------------------------------------

def _make_client() -> Client:
    logger.debug("Initialising Supabase client")
    return create_client(
        SUPABASE_URL,
        SUPABASE_SERVICE_KEY.get(),
    )


_client: Client = _make_client()

# ---------------------------------------------------------------------------
# Site config
# ---------------------------------------------------------------------------

def get_current_week() -> int | None:
    """
    Read current_week from the single-row site_config table.
    Returns None if the table is empty or the value is 0 / unset.
    """
    logger.debug("Fetching current_week from site_config")
    response = (
        _client.table("site_config")
        .select("current_week")
        .limit(1)
        .execute()
    )
    if not response.data:
        logger.warning("site_config returned no rows — current_week is unset")
        return None

    value = response.data[0].get("current_week")
    if not value:
        logger.warning("current_week is 0 or null in site_config")
        return None

    logger.info(f"current_week = {int(value)}")
    return int(value)


# ---------------------------------------------------------------------------
# Blog posts
# ---------------------------------------------------------------------------

def save_blog_post(post: BlogPostInsert) -> str:
    """
    Insert a new blog post row and return the generated UUID as a string.

    Raises:
        Exception: propagates any Supabase / PostgREST error.
    """
    logger.info(f"Saving blog post: '{post.title}'")
    response = (
        _client.table(_BLOG_POSTS_TABLE)
        .insert(post.to_supabase_dict())
        .execute()
    )
    row = response.data[0]
    post_id = str(row["id"])
    logger.info(f"Blog post saved (id={post_id}, title='{post.title}')")
    return post_id


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def save_project(project: ProjectInsert) -> str:
    """
    Insert a new project row and return the generated UUID as a string.

    Raises:
        Exception: propagates any Supabase / PostgREST error.
    """
    logger.info(f"Saving project: '{project.title}'")
    response = (
        _client.table(_PROJECTS_TABLE)
        .insert(project.to_supabase_dict())
        .execute()
    )
    row = response.data[0]
    project_id = str(row["id"])
    logger.info(f"Project saved (id={project_id}, title='{project.title}')")
    return project_id


# ---------------------------------------------------------------------------
# Processed repos
# ---------------------------------------------------------------------------

ProcessedStatus = Literal["success", "skipped", "failed"]


def get_processed_repo_ids() -> set[int]:
    """
    Return the set of GitHub repo IDs that have already been processed
    (any status). Used to diff against the current week's repos.
    """
    logger.debug("Fetching processed repo IDs from agent_processed_repos")
    response = (
        _client.table(_PROCESSED_REPOS_TABLE)
        .select("repo_id")
        .execute()
    )
    ids = {row["repo_id"] for row in response.data}
    logger.info(f"Previously processed repos: {len(ids)}")
    return ids


def mark_repo_processed(
    *,
    repo_id: int,
    repo_name: str,
    status: ProcessedStatus,
    skip_reason: str | None = None,
    blog_post_id: str | None = None,
    raw_llm_output: dict | None = None,
) -> None:
    """
    Upsert a row into agent_processed_repos.

    Args:
        repo_id:        GitHub's permanent numeric repo ID.
        repo_name:      Human-readable "owner/repo" or just repo name.
        status:         "success" | "skipped" | "failed".
        skip_reason:    Populated when status == "skipped".
        blog_post_id:   UUID string of the saved blog post (status == "success").
        raw_llm_output: Raw LLM JSON dict saved for inspection (status == "failed")
                        or failed LinkedIn post content (status == "success").
    """
    logger.debug(f"Marking repo as '{status}': '{repo_name}' (repo_id={repo_id})")

    row: dict = {
        "repo_id": repo_id,
        "repo_name": repo_name,
        "status": status,
        "skip_reason": skip_reason,
        "blog_post_id": blog_post_id,
        "processed_at": datetime.now(tz=timezone.utc).isoformat(),
        "raw_llm_output": raw_llm_output,
    }

    _client.table(_PROCESSED_REPOS_TABLE).upsert(row, on_conflict="repo_id").execute()
    logger.info(
        f"Repo '{repo_name}' (repo_id={repo_id}) marked as '{status}'"
        + (f" — reason: {skip_reason}" if skip_reason else "")
        + (f" — blog_post_id: {blog_post_id}" if blog_post_id else "")
    )