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
        description="Full blog post body in Markdown.",
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