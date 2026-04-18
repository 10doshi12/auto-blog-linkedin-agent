import httpx
from typing import Any

from agent.config.agent_config import config
from agent.config.settings import get_linkedin_publish_settings
from agent.utils.logger import get_logger

logger = get_logger(__name__)

_UGCPOSTS_URL = "https://api.linkedin.com/v2/ugcPosts"


class LinkedInConfigError(ValueError):
    """Raised when LinkedIn publishing is requested without required config."""

    def __init__(self, missing_fields: list[str]) -> None:
        self.missing_fields = missing_fields
        fields = ", ".join(missing_fields)
        super().__init__(f"Missing LinkedIn publishing config: {fields}")


def _get_publish_context() -> tuple[str, dict[str, str]]:
    settings = get_linkedin_publish_settings()
    missing_fields: list[str] = []

    if settings.access_token is None:
        missing_fields.append("LINKEDIN_ACCESS_TOKEN")

    if settings.person_urn is None:
        missing_fields.append("LINKEDIN_PERSON_URN")

    if missing_fields:
        raise LinkedInConfigError(missing_fields)

    headers = {
        "Authorization": f"Bearer {settings.access_token.get()}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    return settings.person_urn, headers


def _build_post_text(text: str, github_url: str | None = None) -> str:
    if github_url:
        suffix = f"\n\nGitHub Repo: {github_url}"
        max_length = config.content.linkedin_post_max_length
        allowed_text_length = max_length - len(suffix)
        if allowed_text_length < 1:
            raise ValueError("LinkedIn post max length is too small to include the GitHub URL suffix")
        text = f"{text[:allowed_text_length].rstrip()}{suffix}"

    return text


def post_to_linkedin(text: str, github_url: str | None = None) -> str:
    """
    Publish a text/link post to the configured LinkedIn personal profile.

    Args:
        text: The ready-to-publish post body (from LLMOutput.linkedin_post).
        github_url: Optional URL to include as a link preview in the post.

    Returns:
        The URN of the created post (from the X-RestLi-Id response header).

    Raises:
        httpx.HTTPStatusError: if LinkedIn returns a non-2xx response.
    """
    person_urn, headers = _get_publish_context()
    text = _build_post_text(text, github_url)

    share_content: dict[str, Any] = {
        "shareCommentary": {
            "text": text,
        },
        "shareMediaCategory": "NONE",
    }

    payload = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": share_content
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
        },
    }

    logger.info("Posting to LinkedIn as %s", person_urn)

    response = httpx.post(_UGCPOSTS_URL, headers=headers, json=payload)
    response.raise_for_status()

    post_urn = response.headers.get("X-RestLi-Id", "unknown")
    logger.info("LinkedIn post published — URN: %s", post_urn)

    return post_urn
