from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from supabase import create_client, Client

from agent.config.settings import settings
from agent.schemas.blog_post import BlogPostInsert

# ---------------------------------------------------------------------------
# Table names
# ---------------------------------------------------------------------------

_BLOG_POSTS_TABLE = "blog_posts"
_PROCESSED_REPOS_TABLE = "agent_processed_repos"

# ---------------------------------------------------------------------------
# Client (module-level singleton — initialised once on first import)
# ---------------------------------------------------------------------------

def _make_client() -> Client:
    return create_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SERVICE_KEY.get(),
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
    response = (
        _client.table("site_config")
        .select("current_week")
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    value = response.data[0].get("current_week")
    return int(value) if value else None


# ---------------------------------------------------------------------------
# Blog posts
# ---------------------------------------------------------------------------

def save_blog_post(post: BlogPostInsert) -> str:
    """
    Insert a new blog post row and return the generated UUID as a string.

    Raises:
        Exception: propagates any Supabase / PostgREST error.
    """
    response = (
        _client.table(_BLOG_POSTS_TABLE)
        .insert(post.to_supabase_dict())
        .execute()
    )
    row = response.data[0]
    return str(row["id"])


# ---------------------------------------------------------------------------
# Processed repos
# ---------------------------------------------------------------------------

ProcessedStatus = Literal["success", "skipped", "failed"]


def get_processed_repo_ids() -> set[int]:
    """
    Return the set of GitHub repo IDs that have already been processed
    (any status). Used to diff against the current week's repos.
    """
    response = (
        _client.table(_PROCESSED_REPOS_TABLE)
        .select("repo_id")
        .execute()
    )
    return {row["repo_id"] for row in response.data}


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
        raw_llm_output: Raw LLM JSON dict saved for inspection (status == "failed").
    """
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