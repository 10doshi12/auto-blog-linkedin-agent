import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from agent.schemas.blog_post import _slugify

_ALLOWED_PROJECT_CATEGORIES = {"ai-ml", "fullstack", "hackathon"}


def normalize_project_category(value: str) -> str:
    category = value.strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "full-stack": "fullstack",
        "fullstack": "fullstack",
        "ai/ml": "ai-ml",
        "aiml": "ai-ml",
    }
    category = aliases.get(category, category)
    if category not in _ALLOWED_PROJECT_CATEGORIES:
        raise ValueError(
            "category must be one of: ai-ml, fullstack, hackathon"
        )
    return category


class Project(BaseModel):
    """
    Full representation of a row in the projects table.
    Used when reading back from Supabase (id and created_at are DB-generated).
    """

    id: uuid.UUID
    source_repo_id: int | None = None
    slug: str
    title: str
    description: str
    content: str | None
    category: Literal["ai-ml", "fullstack", "hackathon"]
    tags: list[str]
    metric: str | None
    github_url: str | None
    live_url: str | None
    has_detail_page: bool
    featured: bool
    display_order: int
    created_at: datetime


class ProjectInsert(BaseModel):
    """
    Payload sent to Supabase when inserting a new project.
    Omits id and created_at — both are set by DB defaults.
    """

    source_repo_id: int
    slug: str
    title: str
    description: str
    content: str | None = None
    category: Literal["ai-ml", "fullstack", "hackathon"]
    tags: list[str] = Field(default_factory=list)
    metric: str | None = None
    github_url: str | None = None
    live_url: str | None = None
    has_detail_page: bool = True
    featured: bool = False
    display_order: int = 99

    @field_validator("category", mode="before")
    @classmethod
    def validate_category(cls, value: str) -> str:
        return normalize_project_category(value)

    @classmethod
    def from_llm_output(
        cls,
        source_repo_id: int,
        slug: str,
        title: str,
        excerpt: str,
        technical_content: str,
        category: str,
        metric: str,
        tags: list[str],
        github_url: str | None = None,
    ) -> "ProjectInsert":
        """
        Construct a ProjectInsert from LLM-generated fields and repo metadata.

        Args:
            slug:               From LLMOutput.slug — short URL slug used by the DB.
            title:              From LLMOutput.title — shared with blog post.
            excerpt:            From LLMOutput.excerpt — used as project description.
            technical_content:  From LLMOutput.technical_content — full technical write-up.
            category:           From LLMOutput.category — "ai-ml", "fullstack", or "hackathon".
            metric:             From LLMOutput.metric — one-line real-world metric.
            tags:               From LLMOutput.tags — shared with blog post.
            github_url:         From repo_obj["html_url"] — the GitHub repo link.
        """
        return cls(
            source_repo_id=source_repo_id,
            slug=_slugify(slug, max_length=30),
            title=title,
            description=excerpt,
            content=technical_content,
            category=category,
            tags=[t.lower().strip() for t in tags],
            metric=metric,
            github_url=github_url,
            live_url=None,
            has_detail_page=True,
            featured=False,
            display_order=99,
        )

    def to_supabase_dict(self) -> dict:
        """
        Serialise to a plain dict suitable for a Supabase insert.
        """
        return self.model_dump()
