from pydantic import BaseModel
from agent.config.settings import GITHUB_USERNAME


class LLMConfig(BaseModel):
    primary_model: str = "mistralai/mistral-7b-instruct"
    fallback_model: str = "mistralai/mistral-small-3.1-24b-instruct:free"
    max_tokens: int = 2048
    temperature: float = 0.7

    model_config = {"frozen": True}


class GitHubConfig(BaseModel):
    username: str = GITHUB_USERNAME
    max_repos_per_run: int = 5
    max_readme_length: int = 8000
    skip_forked_repos: bool = True

    model_config = {"frozen": True}


class ContentConfig(BaseModel):
    blog_post_tone: str = "professional"
    linkedin_post_max_length: int = 2800
    include_hashtags: bool = True
    hashtag_count: int = 5

    model_config = {"frozen": True}


class AgentBehaviourConfig(BaseModel):
    skip_repos_without_readme: bool = True

    model_config = {"frozen": True}


class AgentConfig(BaseModel):
    llm: LLMConfig = LLMConfig()
    github: GitHubConfig = GitHubConfig()
    content: ContentConfig = ContentConfig()
    behaviour: AgentBehaviourConfig = AgentBehaviourConfig()

    model_config = {"frozen": True}


config = AgentConfig()