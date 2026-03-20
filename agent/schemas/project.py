import uuid
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from agent.schemas.blog_post import _slugify


class Project(BaseModel):
    """
    Full representation of a row in the projects table.
    Used when reading back from Supabase (id and created_at are DB-generated).
    """

    id: uuid.UUID
    slug: str
    title: str
    description: str
    content: str | None
    category: str
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

    slug: str
    title: str
    description: str
    content: str | None = None
    category: str
    tags: list[str] = Field(default_factory=list)
    metric: str | None = None
    github_url: str | None = None
    live_url: str | None = None
    has_detail_page: bool = True
    featured: bool = False
    display_order: int = 99

    @classmethod
    def from_llm_output(
        cls,
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
            title:              From LLMOutput.title — shared with blog post.
            excerpt:            From LLMOutput.excerpt — used as project description.
            technical_content:  From LLMOutput.technical_content — full technical write-up.
            category:           From LLMOutput.category — "ai-ml" or "full-stack".
            metric:             From LLMOutput.metric — one-line real-world metric.
            tags:               From LLMOutput.tags — shared with blog post.
            github_url:         From repo_obj["html_url"] — the GitHub repo link.
        """
        return cls(
            slug=_slugify(title),
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