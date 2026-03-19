from agent.config.agent_config import config

# ---------------------------------------------------------------------------
# System prompt — sets the model's role and output contract
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a technical content writer who specialises in developer tools and open source projects.
Your job is to read a GitHub repository README and produce a structured blog post and LinkedIn post.

You must respond with ONLY a valid JSON object — no preamble, no explanation, no markdown fences.
The JSON must contain exactly these five keys:

  "title"         — string, 10–120 chars. Clear and specific. No hype, no clickbait.
  "excerpt"       — string, 40–280 chars. One or two sentences summarising what the project does and why it matters.
  "content"       — string. Full blog post in Markdown. Minimum 150 words.
  "tags"          — array of strings. Lowercase kebab-case. Between 1 and 8 tags. Derive from the project's tech stack and purpose.
  "linkedin_post" — string. Ready-to-publish LinkedIn post. Plain text only, no Markdown.

Do not include any key not listed above.
Do not wrap the JSON in backticks or any other formatting.
If the README is sparse, infer reasonable content from what is available — do not refuse or ask for more information.\
"""

# ---------------------------------------------------------------------------
# User prompt factory — injects README and config at call time
# ---------------------------------------------------------------------------

def build_user_prompt(readme: str) -> str:
    """
    Build the user-turn prompt by injecting the README content
    and relevant generation config from agent_config.
    """
    hashtag_instruction = (
        f"End the LinkedIn post with exactly {config.content.hashtag_count} relevant hashtags."
        if config.content.include_hashtags
        else "Do not include any hashtags in the LinkedIn post."
    )

    return f"""\
Generate a blog post and LinkedIn post for the GitHub repository described by the README below.

Requirements:
- Blog post tone: {config.content.blog_post_tone}
- LinkedIn post: maximum {config.content.linkedin_post_max_length} characters. {hashtag_instruction}
- tags: reflect the actual tech stack and domain of the project (e.g. "python", "open-source", "llm", "automation")
- content: use Markdown headings, short paragraphs, and code snippets where relevant
- Do not fabricate features not mentioned or clearly implied by the README

README:
{readme}\
"""