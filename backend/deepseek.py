from typing import Literal

import httpx
from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.resources import ROOT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: Literal["deepseek"] = "deepseek"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: AnyHttpUrl = AnyHttpUrl("https://api.deepseek.com")
    llm_model: str = Field(default="deepseek-flash", min_length=1)
    generation_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)


class ModelError(Exception):
    pass


class ModelRateLimited(ModelError):
    pass


class DeepSeek:
    def __init__(self, client: httpx.AsyncClient, model: str):
        self.client = client
        self.model = model

    async def complete(self, messages: list[dict[str, str]], *, max_tokens: int) -> str | None:
        try:
            response = await self.client.post("chat/completions", json={
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
                "thinking": {"type": "disabled"},
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            })
            response.raise_for_status()
        except httpx.TimeoutException:
            raise TimeoutError("DeepSeek request timed out") from None
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 429:
                raise ModelRateLimited("DeepSeek rate limit") from None
            raise ModelError(f"DeepSeek HTTP {error.response.status_code}") from None
        except httpx.RequestError as error:
            raise ModelError(f"DeepSeek {type(error).__name__}") from None

        try:
            choice = response.json()["choices"][0]
            content = choice["message"]["content"]
            if choice["finish_reason"] == "stop" and isinstance(content, str):
                return content
        except (ValueError, KeyError, IndexError, TypeError):
            pass
        return None
