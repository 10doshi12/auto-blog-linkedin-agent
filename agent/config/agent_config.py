from pydantic import BaseModel
from agent.config.settings import GITHUB_USERNAME


class LLMConfig(BaseModel):
    primary_model: str = "openai/gpt-oss-20b"
    fallback_model: str = "meta-llama/llama-3.1-8b-instruct"
    max_tokens: int = 8192
    temperature: float = 0.7
    max_concurrent_requests: int = 2
    connect_timeout_seconds: float = 10.0
    write_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 120.0
    pool_timeout_seconds: float = 10.0
    max_retries: int = 2
    shutdown_grace_seconds: float = 5.0

    model_config = {"frozen": True}


class GitHubConfig(BaseModel):
    username: str = GITHUB_USERNAME
    max_repos_per_run: int = 5
    max_readme_length: int = 20000
    skip_forked_repos: bool = True
    per_page: int = 100
    connect_timeout_seconds: float = 10.0
    write_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 30.0
    pool_timeout_seconds: float = 10.0
    max_retries: int = 2

    model_config = {"frozen": True}


class ContentConfig(BaseModel):
    blog_post_tone: str = "professional"
    linkedin_post_max_length: int = 2800
    include_hashtags: bool = True
    hashtag_count: int = 5

    model_config = {"frozen": True}


class AgentBehaviourConfig(BaseModel):
    skip_repos_without_readme: bool = True
    disable_linkedin_posting: bool = True

    model_config = {"frozen": True}


class AgentConfig(BaseModel):
    llm: LLMConfig = LLMConfig()
    github: GitHubConfig = GitHubConfig()
    content: ContentConfig = ContentConfig()
    behaviour: AgentBehaviourConfig = AgentBehaviourConfig()

    model_config = {"frozen": True}


config = AgentConfig()
