import base64
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from agent.config.agent_config import config
from agent.config.settings import GITHUB_TOKEN, GITHUB_USERNAME
from agent.utils.logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
_GITHUB_API_VERSION = "2026-03-10"
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

# Temporary testing override:
# Add repo names here to bypass the weekly range filter and fetch exact repos.
# Example:
# _TEST_REPO_NAMES = ("neogen", "Dynamic-Web-Scraper-Python")
_TEST_REPO_NAMES: tuple[str, ...] = ()


def _build_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN.get()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
    }


def _build_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=config.github.connect_timeout_seconds,
        write=config.github.write_timeout_seconds,
        read=config.github.read_timeout_seconds,
        pool=config.github.pool_timeout_seconds,
    )


def create_github_client(
    *,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    return httpx.Client(
        headers=_build_headers(),
        timeout=_build_timeout(),
        transport=transport,
    )


def _retry_backoff_seconds(attempt: int) -> float:
    return float(2 ** (attempt - 1))


def _request_json(
    client: httpx.Client,
    url: str,
    *,
    allow_404: bool = False,
) -> httpx.Response:
    max_attempts = config.github.max_retries + 1

    for attempt in range(1, max_attempts + 1):
        try:
            logger.debug("GitHub GET %s (attempt=%s)", url, attempt)
            response = client.get(url)

            if allow_404 and response.status_code == 404:
                logger.info("GitHub returned 404 for %s", url)
                return response

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < max_attempts:
                logger.warning(
                    "GitHub retryable status=%s for %s (attempt=%s)",
                    response.status_code,
                    url,
                    attempt,
                )
                time.sleep(_retry_backoff_seconds(attempt))
                continue

            response.raise_for_status()
            return response
        except httpx.TimeoutException as exc:
            logger.warning("GitHub timeout for %s (attempt=%s): %s", url, attempt, exc)
            if attempt >= max_attempts:
                raise
        except httpx.RequestError as exc:
            logger.warning("GitHub transport error for %s (attempt=%s): %s", url, attempt, exc)
            if attempt >= max_attempts:
                raise
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            logger.warning(
                "GitHub HTTP error for %s (attempt=%s, status=%s)",
                url,
                attempt,
                status_code,
            )
            if status_code not in _RETRYABLE_STATUS_CODES or attempt >= max_attempts:
                raise

        time.sleep(_retry_backoff_seconds(attempt))

    raise RuntimeError(f"GitHub request loop exited unexpectedly for url={url}")


def get_current_week_range_ist() -> tuple[datetime, datetime]:
    today_ist = datetime.now(IST)
    week_start = (today_ist - timedelta(days=today_ist.weekday())).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    logger.debug("IST week range: %s → %s", week_start, week_end)
    return week_start, week_end


def _created_at_to_ist(created_at: str) -> datetime:
    created_utc = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return created_utc.astimezone(IST)


def _was_created_in_range(created_at: str, week_start: datetime, week_end: datetime) -> bool:
    created_ist = _created_at_to_ist(created_at)
    return week_start <= created_ist <= week_end


def _decode_readme_content(data: dict[str, Any], *, owner: str, repo_name: str) -> str | None:
    encoded_content = data.get("content")
    if not isinstance(encoded_content, str):
        raise ValueError(f"GitHub README response missing 'content' for {owner}/{repo_name}")

    content = base64.b64decode(encoded_content).decode("utf-8").strip()
    if not content:
        logger.info("README is empty for %s/%s", owner, repo_name)
        return None

    max_length = config.github.max_readme_length
    if max_length > 0 and len(content) > max_length:
        logger.info(
            "Truncating README for %s/%s from %s to %s chars",
            owner,
            repo_name,
            len(content),
            max_length,
        )
        content = content[:max_length]

    logger.debug("README fetched for %s/%s (%s chars)", owner, repo_name, len(content))
    return content


def get_readme_content(client: httpx.Client, owner: str, repo_name: str) -> str | None:
    url = f"https://api.github.com/repos/{owner}/{repo_name}/readme"
    response = _request_json(client, url, allow_404=True)

    if response.status_code == 404:
        logger.info("No README found for %s/%s (404)", owner, repo_name)
        return None

    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected GitHub README payload for {owner}/{repo_name}")

    return _decode_readme_content(data, owner=owner, repo_name=repo_name)


def _validate_repo_payload(repo: Any) -> dict[str, Any]:
    if not isinstance(repo, dict):
        raise ValueError("Unexpected GitHub repo payload entry")

    required_keys = {"id", "name", "created_at", "html_url"}
    missing = [key for key in required_keys if key not in repo]
    if missing:
        raise ValueError(f"GitHub repo payload missing keys: {', '.join(missing)}")

    return repo


def data_to_send_LLM(owner: str) -> list[dict[str, Any]]:
    logger.info("Fetching repos for user: %s", owner)

    if _TEST_REPO_NAMES:
        selected_repo_names = _TEST_REPO_NAMES[: config.github.max_repos_per_run]
        logger.warning(
            "GitHub testing override enabled, bypassing weekly discovery for repos: %s",
            ", ".join(selected_repo_names),
        )
        return [
            manual_repo_fetch(repo_name=repo_name, owner=owner)
            for repo_name in selected_repo_names
        ]

    week_start, week_end = get_current_week_range_ist()
    repos_url = (
        f"https://api.github.com/users/{owner}/repos"
        f"?sort=created&direction=desc&per_page={config.github.per_page}"
    )
    to_process_repos: list[dict[str, Any]] = []
    next_url: str | None = repos_url

    with create_github_client() as client:
        while next_url and len(to_process_repos) < config.github.max_repos_per_run:
            response = _request_json(client, next_url)
            page_repos = response.json()
            if not isinstance(page_repos, list):
                raise ValueError("Unexpected GitHub repos payload shape")

            logger.info("Fetched %s repos from GitHub page", len(page_repos))
            hit_older_repo = False

            for raw_repo in page_repos:
                repo = _validate_repo_payload(raw_repo)
                created_ist = _created_at_to_ist(repo["created_at"])

                if created_ist < week_start:
                    hit_older_repo = True
                    break

                if not _was_created_in_range(repo["created_at"], week_start, week_end):
                    continue

                if config.github.skip_forked_repos and repo.get("fork"):
                    logger.info("Skipping forked repo '%s' (repo_id=%s)", repo["name"], repo["id"])
                    continue

                readme = get_readme_content(client, owner, repo["name"])
                if readme is None and config.behaviour.skip_repos_without_readme:
                    logger.warning(
                        "Skipping repo '%s' (repo_id=%s): no README content",
                        repo["name"],
                        repo["id"],
                    )
                    continue

                process_data = {
                    "repo_obj": repo,
                    "readme": readme or "",
                    "repo_id": repo["id"],
                    "name": repo["name"],
                }
                to_process_repos.append(process_data)
                logger.debug(
                    "Queued repo for processing: '%s' (repo_id=%s)",
                    repo["name"],
                    repo["id"],
                )

                if len(to_process_repos) >= config.github.max_repos_per_run:
                    break

            if hit_older_repo or len(to_process_repos) >= config.github.max_repos_per_run:
                break

            next_url = response.links.get("next", {}).get("url")

    logger.info("Repos ready to process: %s", len(to_process_repos))
    return to_process_repos


def manual_repo_fetch(repo_name: str, owner: str) -> dict[str, Any]:
    logger.info("Manually fetching repo: %s/%s", owner, repo_name)
    url = f"https://api.github.com/repos/{owner}/{repo_name}"

    with create_github_client() as client:
        response = _request_json(client, url)
        repo = _validate_repo_payload(response.json())
        readme = get_readme_content(client, owner=owner, repo_name=repo_name)

    if readme is None:
        logger.warning("Manual fetch: no README content for %s/%s", owner, repo_name)

    process_data = {
        "repo_obj": repo,
        "readme": readme or "",
        "repo_id": repo["id"],
        "name": repo["name"],
    }
    logger.info("Manual fetch complete for '%s' (repo_id=%s)", repo_name, repo["id"])
    return process_data


if __name__ == "__main__":
    data = data_to_send_LLM(owner=GITHUB_USERNAME)
    repo_names = [repo["name"] for repo in data]
    print(repo_names)
    print(manual_repo_fetch(owner=GITHUB_USERNAME, repo_name="neogen"))
