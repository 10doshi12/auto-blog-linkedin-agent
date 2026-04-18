import re
import uuid
from datetime import datetime, timezone
from pydantic import BaseModel, Field

# Average adult reading speed (words per minute)
_WORDS_PER_MINUTE = 200


def _slugify(value: str, *, max_length: int | None = None) -> str:
    """Convert text to a URL-safe slug."""
    slug = value.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)       # strip non-word chars
    slug = re.sub(r"[\s_]+", "-", slug)         # spaces/underscores → hyphens
    slug = re.sub(r"-{2,}", "-", slug)          # collapse multiple hyphens
    slug = slug.strip("-")

    if max_length is not None:
        slug = slug[:max_length].strip("-")
        slug = re.sub(r"-{2,}", "-", slug)

    return slug


def _reading_time(content: str) -> int:
    """Estimate reading time in minutes (minimum 1)."""
    word_count = len(content.split())
    minutes = max(1, round(word_count / _WORDS_PER_MINUTE))
    return minutes


class BlogPost(BaseModel):
    """
    Full representation of a row in the blog_posts table.
    Used when reading back from Supabase (id and created_at are DB-generated).
    """

    id: uuid.UUID
    source_repo_id: int | None = None
    slug: str
    title: str
    excerpt: str
    content: str
    tags: list[str]
    reading_time_minutes: int
    week_number: int | None
    published: bool
    published_at: datetime | None
    created_at: datetime


class BlogPostInsert(BaseModel):
    """
    Payload sent to Supabase when inserting a new blog post.
    Omits id and created_at — both are set by DB defaults.
    """

    source_repo_id: int
    slug: str
    title: str
    excerpt: str
    content: str
    tags: list[str] = Field(default_factory=list)
    reading_time_minutes: int = Field(default=1, ge=1)
    week_number: int | None = None
    published: bool = False
    published_at: datetime | None = None

    @classmethod
    def from_llm_output(
        cls,
        source_repo_id: int,
        slug: str,
        title: str,
        excerpt: str,
        content: str,
        tags: list[str],
        week_number: int | None = None,
    ) -> "BlogPostInsert":
        """
        Construct a BlogPostInsert from raw LLM-generated fields.
        week_number should be passed in from site_config.current_week in Supabase.
        reading_time_minutes is derived automatically.
        """
        return cls(
            source_repo_id=source_repo_id,
            slug=_slugify(slug, max_length=30),
            title=title,
            excerpt=excerpt,
            content=content,
            tags=[t.lower().strip() for t in tags],
            reading_time_minutes=_reading_time(content),
            week_number=week_number,
            published=True,
            published_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_supabase_dict(self) -> dict:
        """
        Serialise to a plain dict suitable for a Supabase insert.
        Converts datetime objects to ISO 8601 strings.
        """
        data = self.model_dump()
        if data["published_at"] is not None:
            data["published_at"] = data["published_at"].isoformat()
        return data
