import httpx
from agent.config.settings import LINKEDIN_ACCESS_TOKEN

token = LINKEDIN_ACCESS_TOKEN.get()

# First confirm who the token belongs to
r = httpx.get(
    "https://api.linkedin.com/v2/userinfo",
    headers={
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": "202504",
    }
)

print(r.status_code)
print(r.json())