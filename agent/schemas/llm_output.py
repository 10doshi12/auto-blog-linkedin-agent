import re
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator

from agent.schemas.blog_post import _slugify
from agent.schemas.project import normalize_project_category


class LLMOutput(BaseModel):
    """
    Validated structure of the JSON response expected from the LLM.
    The LLM prompt must instruct the model to return exactly these fields.
    """

    slug: str = Field(
        ...,
        min_length=3,
        max_length=30,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        description="Short kebab-case slug for URLs, max 30 chars.",
    )
    title: str = Field(
        ...,
        min_length=5,
        max_length=200,
        description="Blog post title — clear, specific, no clickbait.",
    )
    excerpt: str = Field(
        ...,
        min_length=20,
        max_length=400,
        description="One or two sentence summary shown in post listings.",
    )
    content: str = Field(
        ...,
        min_length=100,
        description="Full blog post body in Markdown — accessible, non-technical, story-driven.",
    )
    technical_content: str = Field(
        ...,
        min_length=100,
        description="Technical deep-dive in Markdown — architecture, stack, implementation details.",
    )
    category: Literal["ai-ml", "fullstack", "hackathon"] = Field(
        ...,
        description="Project category inferred from the README. Must be exactly 'ai-ml', 'fullstack', or 'hackathon'.",
    )
    metric: str = Field(
        ...,
        min_length=5,
        max_length=100,
        description="One-line real-world performance or impact metric, e.g. 'Reduces deployment time by ~60%'.",
    )
    tags: list[str] = Field(
        ...,
        min_length=1,
        max_length=8,
        description="Lowercase kebab-case tags, e.g. ['open-source', 'python'].",
    )
    linkedin_post: str = Field(
        ...,
        min_length=30,
        max_length=3000,
        description="Ready-to-publish LinkedIn post text (no extra formatting).",
    )

    @field_validator("slug", mode="before")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        slug = _slugify(value, max_length=30)
        if not slug:
            raise ValueError("slug must contain at least one alphanumeric character")
        return slug

    @field_validator("category", mode="before")
    @classmethod
    def validate_category(cls, value: str) -> str:
        return normalize_project_category(value)

    @field_validator("tags", mode="before")
    @classmethod
    def validate_tags(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("tags must be a list of strings")

        normalized_tags: list[str] = []
        seen: set[str] = set()
        for raw_tag in value:
            if not isinstance(raw_tag, str):
                raise ValueError("each tag must be a string")

            tag = raw_tag.strip().lower()
            tag = re.sub(r"[^\w\s-]", "", tag)
            tag = re.sub(r"[\s_]+", "-", tag)
            tag = re.sub(r"-{2,}", "-", tag).strip("-")

            if not tag:
                raise ValueError("tags cannot be empty after normalization")

            if tag not in seen:
                normalized_tags.append(tag)
                seen.add(tag)

        return normalized_tags
