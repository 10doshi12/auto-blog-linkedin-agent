import json
import re
import httpx
from agent.config.settings import OPENROUTER_API_KEY
from agent.config.agent_config import config
from agent.schemas.llm_output import LLMOutput
from agent.prompts.templates import build_prompt
from agent.utils.logger import get_logger
from pydantic import ValidationError
logger = get_logger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

HEADERS = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY.get()}",
    "HTTP-Referer": "https://github.com/yourusername/auto-blog-agent",
    "X-OpenRouter-Title": "auto-blog-agent",
    "Content-Type": "application/json",
}
def _sanitize_raw(raw: str) -> str:
    # Remove markdown fences
    raw = re.sub(r"^```json\s*", "", raw.strip())
    raw = re.sub(r"```\s*$", "", raw.strip())

    # Use a JSON string-aware replacer to escape control chars inside values
    result = []
    in_string = False
    escaped = False

    for char in raw:
        if escaped:
            result.append(char)
            escaped = False
        elif char == "\\":
            result.append(char)
            escaped = True
        elif char == '"':
            in_string = not in_string
            result.append(char)
        elif in_string and ord(char) < 0x20:
            # Escape control characters only when inside a string value
            replacements = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
            result.append(replacements.get(char, ""))
        else:
            result.append(char)

    return "".join(result)

def _call_model(readme: str, model: str) -> LLMOutput:
    system_prompt, user_prompt = build_prompt(readme)

    payload = {
        "model": model,
        "max_tokens": config.llm.max_tokens,
        "temperature": config.llm.temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    response = httpx.post(OPENROUTER_URL, headers=HEADERS, data=json.dumps(payload))
    response.raise_for_status()

    raw_content = response.json()["choices"][0]["message"]["content"]

    parsed = json.loads(_sanitize_raw(raw_content))
    return LLMOutput.model_validate(parsed)


def generate_blog_content(readme: str) -> LLMOutput:
    try:
        logger.info(f"Calling primary model: {config.llm.primary_model}")
        return _call_model(readme, config.llm.primary_model)

    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning(f"Primary model failed: {e}. Retrying with fallback model.")

    try:
        logger.info(f"Calling fallback model: {config.llm.fallback_model}")
        return _call_model(readme, config.llm.fallback_model)

    except (json.JSONDecodeError, ValidationError) as e:
        logger.error(f"Fallback model also failed: {e}")
        raise

def check_openrouter_connection() -> bool:
    try:
        response = httpx.get(
            "https://openrouter.ai/api/v1/models",
            headers=HEADERS,
        )
        response.raise_for_status()
        logger.info("OpenRouter connection successful.")
        return True
    except httpx.HTTPStatusError as e:
        logger.error(f"OpenRouter connection failed — HTTP {e.response.status_code}")
        return False
    except httpx.RequestError as e:
        logger.error(f"OpenRouter connection failed — network error: {e}")
        return False