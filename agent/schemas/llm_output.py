from typing import Literal
from pydantic import BaseModel, Field


class LLMOutput(BaseModel):
    """
    Validated structure of the JSON response expected from the LLM.
    The LLM prompt must instruct the model to return exactly these fields.
    """

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
    category: Literal["ai-ml", "full-stack"] = Field(
        ...,
        description="Project category inferred from the README. Must be exactly 'ai-ml' or 'full-stack'.",
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