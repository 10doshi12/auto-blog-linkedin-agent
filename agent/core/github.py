import httpx
from agent.config.settings import GITHUB_TOKEN, GITHUB_USERNAME

repos_url = f'https://api.github.com/users/{GITHUB_USERNAME}/repos'

headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN.get()}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2026-03-10",
}

# for obj in r.json():
#     print(obj["id"])
#     print(obj["created_at"][:10])
#     print(obj["name"])
#     print("\n")

def fetch_from_github(url:str)->object:
    r = httpx.get(url, headers=headers)
    return r


# # print(r.json()[0]["created_at"])
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

def get_current_week_range_ist() -> tuple[datetime, datetime]:
    today_ist = datetime.now(IST)
    week_start = (today_ist - timedelta(days=today_ist.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
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
        return None  # README does not exist

    data = r.json()

    import base64
    content = base64.b64decode(data["content"]).decode("utf-8").strip()

    if not content:
        return None  # README exists but is empty

    return content

def data_to_send_LLM() -> list:
    r = fetch_from_github(url=repos_url)
    new_repos_this_week = [
        repo for repo in r.json()
        if "2025" in repo["created_at"]
        # if was_created_this_week(repo["created_at"])
    ]
    
    to_process_repos = []
    for repo in new_repos_this_week:
        readme = get_readme_content(GITHUB_USERNAME, repo["name"])

        if readme is None:
            # log skip reason, mark as skipped in Supabase, continue
            # print(f"Readme is Empty : for repo id {repo["id"]}")
            continue
        
        #storing in readme content in dictionary with the repo object
        process_data = {
            "repo_obj" : repo,
            "readme": readme,
            "repo_id":repo["id"],
            "name":repo["name"]
        }
        to_process_repos.append(process_data)
    return to_process_repos
    
def manaul_repo_fetch(repo_name:str, owner:str)->dict:
    url = f"https://api.github.com/repos/{owner}/{repo_name}"
    r = fetch_from_github(url=url)
    repo = r.json()
    readme=get_readme_content(owner=owner, repo_name=repo_name)
    process_data = {
            "repo_obj" : repo,
            "readme": readme,
            "repo_id":repo["id"],
            "name":repo["name"]
        }
    return process_data

# Quick sanity check — run this and confirm the dates look right
if __name__ == "__main__":
    data = data_to_send_LLM()
    repo_names = [repo["name"] for repo in data]
    print(repo_names)
    print(manaul_repo_fetch(owner=GITHUB_USERNAME,repo_name="neogen"))
    


