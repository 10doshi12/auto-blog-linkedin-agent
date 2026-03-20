import httpx

from agent.config.settings import LINKEDIN_ACCESS_TOKEN, LINKEDIN_PERSON_URN
from agent.utils.logger import get_logger

logger = get_logger(__name__)

_UGCPOSTS_URL = "https://api.linkedin.com/v2/ugcPosts"

_HEADERS = {
    "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN.get()}",
    "Content-Type": "application/json",
    "X-Restli-Protocol-Version": "2.0.0",
}


def post_to_linkedin(text: str) -> str:
    """
    Publish a text post to the configured LinkedIn personal profile.

    Args:
        text: The ready-to-publish post body (from LLMOutput.linkedin_post).

    Returns:
        The URN of the created post (from the X-RestLi-Id response header).

    Raises:
        httpx.HTTPStatusError: if LinkedIn returns a non-2xx response.
    """
    person_urn = LINKEDIN_PERSON_URN

    payload = {
        "author": f"urn:li:person:{person_urn}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {
                    "text": text,
                },
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
        },
    }

    logger.info("Posting to LinkedIn as urn:li:person:%s", person_urn)

    response = httpx.post(_UGCPOSTS_URL, headers=_HEADERS, json=payload)
    response.raise_for_status()

    post_urn = response.headers.get("X-RestLi-Id", "unknown")
    logger.info("LinkedIn post published — URN: %s", post_urn)

    return post_urn