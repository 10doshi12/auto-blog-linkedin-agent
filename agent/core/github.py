import base64
from datetime import datetime, timezone, timedelta

import httpx

from agent.config.settings import GITHUB_TOKEN, GITHUB_USERNAME
from agent.utils.logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN.get()}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2026-03-10",
}


def fetch_from_github(url: str) -> httpx.Response:
    logger.debug(f"GET {url}")
    r = httpx.get(url, headers=headers)
    logger.debug(f"Response {r.status_code} from {url}")
    return r


def get_current_week_range_ist() -> tuple[datetime, datetime]:
    today_ist = datetime.now(IST)
    week_start = (today_ist - timedelta(days=today_ist.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    logger.debug(f"IST week range: {week_start} → {week_end}")
    return week_start, week_end


def was_created_this_week(created_at: str) -> bool:
    created_utc = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    created_ist = created_utc.astimezone(IST)
    week_start, week_end = get_current_week_range_ist()
    return week_start <= created_ist <= week_end


def get_readme_content(owner: str, repo_name: str) -> str | None:
    url = f"https://api.github.com/repos/{owner}/{repo_name}/readme"
    r = fetch_from_github(url=url)

    if r.status_code == 404:
        logger.info(f"No README found for {owner}/{repo_name} (404)")
        return None

    data = r.json()
    content = base64.b64decode(data["content"]).decode("utf-8").strip()

    if not content:
        logger.info(f"README is empty for {owner}/{repo_name}")
        return None

    logger.debug(f"README fetched for {owner}/{repo_name} ({len(content)} chars)")
    return content


def data_to_send_LLM(owner: str) -> list:
    logger.info(f"Fetching repos for user: {owner}")
    repos_url = f"https://api.github.com/users/{owner}/repos"
    r = fetch_from_github(url=repos_url)

    all_repos = r.json()
    logger.info(f"Total public repos fetched: {len(all_repos)}")

    new_repos_this_week = [
        repo for repo in all_repos
        if was_created_this_week(repo["created_at"])
    ]
    logger.info(f"Repos created this IST week: {len(new_repos_this_week)}")

    to_process_repos = []
    for repo in new_repos_this_week:
        readme = get_readme_content(owner, repo["name"])

        if readme is None:
            logger.warning(f"Skipping repo '{repo['name']}' (repo_id={repo['id']}): no README content")
            continue

        process_data = {
            "repo_obj": repo,
            "readme": readme,
            "repo_id": repo["id"],
            "name": repo["name"],
        }
        to_process_repos.append(process_data)
        logger.debug(f"Queued repo for processing: '{repo['name']}' (repo_id={repo['id']})")

    logger.info(f"Repos ready to process: {len(to_process_repos)}")
    return to_process_repos


def manual_repo_fetch(repo_name: str, owner: str) -> dict:
    logger.info(f"Manually fetching repo: {owner}/{repo_name}")
    url = f"https://api.github.com/repos/{owner}/{repo_name}"
    r = fetch_from_github(url=url)
    repo = r.json()

    readme = get_readme_content(owner=owner, repo_name=repo_name)
    if readme is None:
        logger.warning(f"Manual fetch: no README content for {owner}/{repo_name}")

    process_data = {
        "repo_obj": repo,
        "readme": readme,
        "repo_id": repo["id"],
        "name": repo["name"],
    }
    logger.info(f"Manual fetch complete for '{repo_name}' (repo_id={repo['id']})")
    return process_data


if __name__ == "__main__":
    data = data_to_send_LLM(owner=GITHUB_USERNAME)
    repo_names = [repo["name"] for repo in data]
    print(repo_names)
    print(manual_repo_fetch(owner=GITHUB_USERNAME, repo_name="neogen"))