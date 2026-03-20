from pydantic import BaseModel
from agent.config.settings import GITHUB_USERNAME


class LLMConfig(BaseModel):
    primary_model: str = "openai/gpt-oss-20b"
    fallback_model: str = "meta-llama/llama-3.1-8b-instruct"
    max_tokens: int = 8192
    temperature: float = 0.7

    model_config = {"frozen": True}


class GitHubConfig(BaseModel):
    username: str = GITHUB_USERNAME
    max_repos_per_run: int = 5
    max_readme_length: int = 20000
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