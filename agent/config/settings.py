import os
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class SecretStr:
    """
    Wraps a sensitive string so it cannot be accidentally printed or logged.
    Use .get() to access the actual value when passing it to an API client.

    >>> token = SecretStr("super-secret")
    >>> print(token)         # **********
    >>> str(token)           # **********
    >>> repr(token)          # SecretStr(**********) 
    >>> token.get()          # super-secret
    """

    def __init__(self, value: str) -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def __str__(self) -> str:
        return "**********"

    def __repr__(self) -> str:
        return "SecretStr(**********)"

    def __bool__(self) -> bool:
        return bool(self._value)


def _require_secret(key: str) -> SecretStr:
    """Load a required secret from the environment. Raises KeyError if missing."""
    return SecretStr(os.environ[key])


def _optional_value(key: str) -> str | None:
    """Load an optional environment variable and treat blank values as missing."""
    value = os.getenv(key)
    if value is None:
        return None

    value = value.strip()
    return value or None


def _optional_secret(key: str) -> SecretStr | None:
    """Load an optional secret from the environment. Returns None if missing."""
    value = _optional_value(key)
    return SecretStr(value) if value else None


class LinkedInPublishSettings(BaseModel):
    access_token: SecretStr | None
    person_urn: str | None

    model_config = {"frozen": True, "arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------
GITHUB_TOKEN: SecretStr = _require_secret("GITHUB_TOKEN")
GITHUB_USERNAME: str = os.environ["GITHUB_USERNAME"]

# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------
OPENROUTER_API_KEY: SecretStr = _require_secret("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_HTTP_REFERER: str = os.getenv("OPENROUTER_HTTP_REFERER", "")
OPENROUTER_APP_TITLE: str = os.getenv("OPENROUTER_APP_TITLE", "auto-blog-agent")

# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------
SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY: SecretStr = _require_secret("SUPABASE_SERVICE_KEY")

# ---------------------------------------------------------------------------
# LinkedIn (optional — only required if LinkedIn posting is enabled)
# ---------------------------------------------------------------------------


def get_linkedin_client_id() -> SecretStr | None:
    return _optional_secret("LINKEDIN_CLIENT_ID")


def get_linkedin_client_secret() -> SecretStr | None:
    return _optional_secret("LINKEDIN_CLIENT_SECRET")


def get_linkedin_access_token() -> SecretStr | None:
    return _optional_secret("LINKEDIN_ACCESS_TOKEN")


def get_linkedin_person_urn() -> str | None:
    return _optional_value("LINKEDIN_PERSON_URN")


def get_linkedin_publish_settings() -> LinkedInPublishSettings:
    return LinkedInPublishSettings(
        access_token=get_linkedin_access_token(),
        person_urn=get_linkedin_person_urn(),
    )

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
DRY_RUN: bool = os.getenv("DRY_RUN", "false").lower() == "true"
