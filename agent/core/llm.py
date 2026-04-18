import asyncio
import json
import re
import time

import httpx
from pydantic import ValidationError

from agent.config.agent_config import config
from agent.config.settings import (
    OPENROUTER_API_KEY,
    OPENROUTER_APP_TITLE,
    OPENROUTER_BASE_URL,
    OPENROUTER_HTTP_REFERER,
)
from agent.prompts.templates import build_prompt
from agent.schemas.llm_output import LLMOutput
from agent.utils.logger import get_logger

logger = get_logger(__name__)

_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


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
            replacements = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
            result.append(replacements.get(char, ""))
        else:
            result.append(char)

    return "".join(result)


def _build_headers() -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY.get()}",
        "Content-Type": "application/json",
    }

    if OPENROUTER_HTTP_REFERER:
        headers["HTTP-Referer"] = OPENROUTER_HTTP_REFERER

    if OPENROUTER_APP_TITLE:
        headers["X-OpenRouter-Title"] = OPENROUTER_APP_TITLE

    return headers


def _build_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=config.llm.connect_timeout_seconds,
        write=config.llm.write_timeout_seconds,
        read=config.llm.read_timeout_seconds,
        pool=config.llm.pool_timeout_seconds,
    )


def create_openrouter_client(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"{OPENROUTER_BASE_URL.rstrip('/')}/",
        headers=_build_headers(),
        timeout=_build_timeout(),
        transport=transport,
    )


def _build_payload(readme: str, model: str) -> dict:
    system_prompt, user_prompt = build_prompt(readme)
    return {
        "model": model,
        "max_tokens": config.llm.max_tokens,
        "temperature": config.llm.temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }


def _retry_backoff_seconds(attempt: int) -> float:
    return float(2 ** (attempt - 1))


def _log_request_start(repo_name: str, model: str, attempt: int) -> float:
    started_at = time.monotonic()
    logger.info(
        "[%s] OpenRouter request starting (model=%s, attempt=%s, timeout=%.1fs)",
        repo_name,
        model,
        attempt,
        config.llm.read_timeout_seconds,
    )
    return started_at


def _duration_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


async def _call_model(
    client: httpx.AsyncClient,
    *,
    readme: str,
    model: str,
    repo_name: str,
) -> LLMOutput:
    payload = _build_payload(readme, model)
    max_attempts = config.llm.max_retries + 1

    for attempt in range(1, max_attempts + 1):
        started_at = _log_request_start(repo_name, model, attempt)

        try:
            response = await client.post("chat/completions", json=payload)

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < max_attempts:
                logger.warning(
                    "[%s] OpenRouter retryable status=%s (model=%s, attempt=%s, duration_ms=%s)",
                    repo_name,
                    response.status_code,
                    model,
                    attempt,
                    _duration_ms(started_at),
                )
                await asyncio.sleep(_retry_backoff_seconds(attempt))
                continue

            response.raise_for_status()

            raw_content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(_sanitize_raw(raw_content))
            validated = LLMOutput.model_validate(parsed)

            logger.info(
                "[%s] OpenRouter request succeeded (model=%s, attempt=%s, duration_ms=%s)",
                repo_name,
                model,
                attempt,
                _duration_ms(started_at),
            )
            return validated

        except asyncio.CancelledError:
            logger.warning(
                "[%s] OpenRouter request cancelled (model=%s, attempt=%s, duration_ms=%s)",
                repo_name,
                model,
                attempt,
                _duration_ms(started_at),
            )
            raise
        except (json.JSONDecodeError, ValidationError):
            logger.warning(
                "[%s] OpenRouter response validation failed (model=%s, attempt=%s, duration_ms=%s)",
                repo_name,
                model,
                attempt,
                _duration_ms(started_at),
            )
            raise
        except httpx.TimeoutException as exc:
            logger.warning(
                "[%s] OpenRouter timeout (model=%s, attempt=%s, duration_ms=%s): %s",
                repo_name,
                model,
                attempt,
                _duration_ms(started_at),
                exc,
            )
            if attempt >= max_attempts:
                raise
        except httpx.RequestError as exc:
            logger.warning(
                "[%s] OpenRouter transport error (model=%s, attempt=%s, duration_ms=%s): %s",
                repo_name,
                model,
                attempt,
                _duration_ms(started_at),
                exc,
            )
            if attempt >= max_attempts:
                raise
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            logger.warning(
                "[%s] OpenRouter HTTP error (model=%s, attempt=%s, status=%s, duration_ms=%s)",
                repo_name,
                model,
                attempt,
                status_code,
                _duration_ms(started_at),
            )
            if status_code not in _RETRYABLE_STATUS_CODES or attempt >= max_attempts:
                raise

        await asyncio.sleep(_retry_backoff_seconds(attempt))

    raise RuntimeError(f"[{repo_name}] OpenRouter request loop exited unexpectedly for model={model}")


async def generate_blog_content(
    client: httpx.AsyncClient,
    *,
    readme: str,
    repo_name: str,
) -> LLMOutput:
    try:
        logger.info("[%s] Calling primary model: %s", repo_name, config.llm.primary_model)
        return await _call_model(
            client,
            readme=readme,
            model=config.llm.primary_model,
            repo_name=repo_name,
        )
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning(
            "[%s] Primary model output invalid: %s. Retrying with fallback model.",
            repo_name,
            exc,
        )

    logger.info("[%s] Calling fallback model: %s", repo_name, config.llm.fallback_model)
    return await _call_model(
        client,
        readme=readme,
        model=config.llm.fallback_model,
        repo_name=repo_name,
    )


async def check_openrouter_connection(client: httpx.AsyncClient) -> bool:
    max_attempts = config.llm.max_retries + 1

    for attempt in range(1, max_attempts + 1):
        try:
            logger.info("Checking OpenRouter connection (attempt=%s)", attempt)
            response = await client.get("models")

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < max_attempts:
                logger.warning(
                    "OpenRouter connection check got retryable status=%s (attempt=%s)",
                    response.status_code,
                    attempt,
                )
                await asyncio.sleep(_retry_backoff_seconds(attempt))
                continue

            response.raise_for_status()
            logger.info("OpenRouter connection successful.")
            return True
        except httpx.TimeoutException as exc:
            logger.warning("OpenRouter connection timeout (attempt=%s): %s", attempt, exc)
            if attempt >= max_attempts:
                break
        except httpx.RequestError as exc:
            logger.warning("OpenRouter connection request error (attempt=%s): %s", attempt, exc)
            if attempt >= max_attempts:
                break
        except httpx.HTTPStatusError as exc:
            logger.error("OpenRouter connection failed — HTTP %s", exc.response.status_code)
            if exc.response.status_code not in _RETRYABLE_STATUS_CODES or attempt >= max_attempts:
                break

        await asyncio.sleep(_retry_backoff_seconds(attempt))

    return False
