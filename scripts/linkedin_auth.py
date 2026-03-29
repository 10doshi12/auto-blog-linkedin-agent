import httpx

from agent.config.settings import get_linkedin_access_token


def main() -> None:
    token = get_linkedin_access_token()
    if token is None:
        raise SystemExit("LINKEDIN_ACCESS_TOKEN is required to run this helper script.")

    response = httpx.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={
            "Authorization": f"Bearer {token.get()}",
            "LinkedIn-Version": "202504",
        },
    )
    response.raise_for_status()

    print(response.status_code)
    print(response.json())


if __name__ == "__main__":
    main()
