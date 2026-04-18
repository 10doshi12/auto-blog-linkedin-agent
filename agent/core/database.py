from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from postgrest import APIError
from pydantic import BaseModel
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
_PERSIST_REPO_RESULT_RPC = "persist_repo_result"

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


class PersistRepoOutcome(BaseModel):
    blog_post_id: str | None = None
    project_id: str | None = None

    model_config = {"frozen": True}


def _api_error_fields(exc: APIError) -> dict[str, str | None]:
    payload = exc.json()
    return {
        "code": payload.get("code"),
        "message": payload.get("message"),
        "details": payload.get("details"),
        "hint": payload.get("hint"),
    }


def _blog_post_debug_summary(post: BlogPostInsert) -> dict[str, object]:
    return {
        "slug": post.slug,
        "title": post.title,
        "tag_count": len(post.tags),
        "content_length": len(post.content),
        "excerpt_length": len(post.excerpt),
        "reading_time_minutes": post.reading_time_minutes,
        "week_number": post.week_number,
        "published": post.published,
    }


def _project_debug_summary(project: ProjectInsert) -> dict[str, object]:
    return {
        "slug": project.slug,
        "title": project.title,
        "category": project.category,
        "tag_count": len(project.tags),
        "description_length": len(project.description),
        "content_length": len(project.content or ""),
        "metric_length": len(project.metric or ""),
        "github_url": project.github_url,
        "live_url": project.live_url,
        "has_detail_page": project.has_detail_page,
        "featured": project.featured,
        "display_order": project.display_order,
    }


def _rpc_payload_summary(
    *,
    repo_id: int,
    repo_name: str,
    status: ProcessedStatus,
    blog_post: BlogPostInsert | None,
    project: ProjectInsert | None,
    skip_reason: str | None,
    raw_llm_output: dict | None,
) -> dict[str, object]:
    return {
        "repo_id": repo_id,
        "repo_name": repo_name,
        "status": status,
        "has_blog_post": blog_post is not None,
        "has_project": project is not None,
        "skip_reason": skip_reason,
        "raw_llm_output_keys": sorted(raw_llm_output.keys()) if raw_llm_output else [],
    }


def _coerce_rpc_row(data: object) -> dict[str, object]:
    if isinstance(data, list):
        if not data:
            return {}
        first_row = data[0]
        return first_row if isinstance(first_row, dict) else {}

    if isinstance(data, dict):
        return data

    return {}

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
# Transactional repo persistence
# ---------------------------------------------------------------------------

def persist_repo_result(
    *,
    repo_id: int,
    repo_name: str,
    status: ProcessedStatus,
    blog_post: BlogPostInsert | None = None,
    project: ProjectInsert | None = None,
    skip_reason: str | None = None,
    raw_llm_output: dict | None = None,
    processed_at: datetime | None = None,
) -> PersistRepoOutcome:
    logger.info(
        "Persisting repo result via RPC '%s': repo='%s' status='%s'",
        _PERSIST_REPO_RESULT_RPC,
        repo_name,
        status,
    )
    params = {
        "p_repo_id": repo_id,
        "p_repo_name": repo_name,
        "p_status": status,
        "p_skip_reason": skip_reason,
        "p_blog_post": blog_post.to_supabase_dict() if blog_post is not None else None,
        "p_project": project.to_supabase_dict() if project is not None else None,
        "p_raw_llm_output": raw_llm_output,
        "p_processed_at": (processed_at or datetime.now(tz=timezone.utc)).isoformat(),
    }

    try:
        response = _client.rpc(_PERSIST_REPO_RESULT_RPC, params).execute()
    except APIError as exc:
        details = _api_error_fields(exc)
        logger.error(
            "Supabase RPC '%s' failed: code=%s message=%s details=%s hint=%s payload=%s",
            _PERSIST_REPO_RESULT_RPC,
            details["code"],
            details["message"],
            details["details"],
            details["hint"],
            _rpc_payload_summary(
                repo_id=repo_id,
                repo_name=repo_name,
                status=status,
                blog_post=blog_post,
                project=project,
                skip_reason=skip_reason,
                raw_llm_output=raw_llm_output,
            ),
        )
        raise

    row = _coerce_rpc_row(response.data)
    outcome = PersistRepoOutcome(
        blog_post_id=row.get("blog_post_id"),
        project_id=row.get("project_id"),
    )
    logger.info(
        "RPC '%s' persisted repo='%s' status='%s' (blog_post_id=%s, project_id=%s)",
        _PERSIST_REPO_RESULT_RPC,
        repo_name,
        status,
        outcome.blog_post_id,
        outcome.project_id,
    )
    return outcome


def mark_repo_in_progress(
    *,
    repo_id: int,
    repo_name: str,
) -> PersistRepoOutcome:
    return persist_repo_result(
        repo_id=repo_id,
        repo_name=repo_name,
        status="in_progress",
    )


# ---------------------------------------------------------------------------
# Blog posts (legacy direct inserts; retained for compatibility/debugging)
# ---------------------------------------------------------------------------

def save_blog_post(post: BlogPostInsert) -> str:
    """
    Insert a new blog post row and return the generated UUID as a string.

    Raises:
        Exception: propagates any Supabase / PostgREST error.
    """
    logger.info(f"Saving blog post: '{post.title}'")
    try:
        response = (
            _client.table(_BLOG_POSTS_TABLE)
            .insert(post.to_supabase_dict())
            .execute()
        )
    except APIError as exc:
        details = _api_error_fields(exc)
        logger.error(
            "Supabase insert into '%s' failed for blog post '%s': code=%s message=%s details=%s hint=%s payload=%s",
            _BLOG_POSTS_TABLE,
            post.title,
            details["code"],
            details["message"],
            details["details"],
            details["hint"],
            _blog_post_debug_summary(post),
        )
        raise
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
    try:
        response = (
            _client.table(_PROJECTS_TABLE)
            .insert(project.to_supabase_dict())
            .execute()
        )
    except APIError as exc:
        details = _api_error_fields(exc)
        logger.error(
            "Supabase insert into '%s' failed for project '%s': code=%s message=%s details=%s hint=%s payload=%s",
            _PROJECTS_TABLE,
            project.title,
            details["code"],
            details["message"],
            details["details"],
            details["hint"],
            _project_debug_summary(project),
        )
        raise
    row = response.data[0]
    project_id = str(row["id"])
    logger.info(f"Project saved (id={project_id}, title='{project.title}')")
    return project_id


# ---------------------------------------------------------------------------
# Processed repos
# ---------------------------------------------------------------------------

ProcessedStatus = Literal["in_progress", "success", "skipped", "failed", "cancelled"]


def get_processed_repo_ids() -> set[int]:
    """
    Return the set of GitHub repo IDs that have already been processed
    (any status). Used to diff against the current week's repos.
    """
    logger.debug("Fetching processed repo IDs from agent_processed_repos")
    response = (
        _client.table("agent_processed_repos")
        .select("repo_id")
        .in_("status", ["success", "skipped"])
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
        status:         "success" | "skipped" | "failed" | "cancelled".
        skip_reason:    Populated when status == "skipped".
        blog_post_id:   UUID string of the saved blog post (status == "success").
        raw_llm_output: Validated LLM JSON dict saved for inspection on every
                        processed repo, including LinkedIn status metadata.
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

    try:
        _client.table(_PROCESSED_REPOS_TABLE).upsert(row, on_conflict="repo_id").execute()
    except APIError as exc:
        details = _api_error_fields(exc)
        logger.error(
            "Supabase upsert into '%s' failed for repo_id=%s: code=%s message=%s details=%s hint=%s status=%s",
            _PROCESSED_REPOS_TABLE,
            repo_id,
            details["code"],
            details["message"],
            details["details"],
            details["hint"],
            status,
        )
        raise
    logger.info(
        f"Repo '{repo_name}' (repo_id={repo_id}) marked as '{status}'"
        + (f" — reason: {skip_reason}" if skip_reason else "")
        + (f" — blog_post_id: {blog_post_id}" if blog_post_id else "")
    )
