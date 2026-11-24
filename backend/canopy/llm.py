"""Server-only Gemini adapter. No financial or database actions are exposed to it."""

from dataclasses import dataclass
from typing import Protocol

from google import genai
from google.genai import types

from canopy.config import Settings


class ModelUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


class LanguageModel(Protocol):
    def generate(self, prompt: str, *, system: str) -> ModelResult: ...


class GeminiModel:
    def __init__(self, settings: Settings, *, client=None):
        self.settings = settings
        if client is None and not settings.gemini_api_key.get_secret_value():
            raise ModelUnavailable("Set GEMINI_API_KEY in backend/.env before using Gemini")
        self.client = client or genai.Client(
            api_key=settings.gemini_api_key.get_secret_value(),
            http_options=types.HttpOptions(
                timeout=int(settings.gemini_timeout_seconds * 1000),
                # No implicit retry loops or unexpected additional billed requests.
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    def generate(self, prompt: str, *, system: str) -> ModelResult:
        if len(prompt) + len(system) > self.settings.gemini_max_input_characters:
            raise ValueError("Input exceeds the configured Gemini input limit")
        try:
            response = self.client.models.generate_content(
                model=self.settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=self.settings.gemini_max_output_tokens,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            candidates = response.candidates or []
            if not candidates or str(candidates[0].finish_reason) not in {
                "STOP",
                "FinishReason.STOP",
            }:
                raise ModelUnavailable("Gemini did not return a complete response")
            text = response.text
            if not text or not text.strip():
                raise ModelUnavailable("Gemini returned no usable response")
            usage = response.usage_metadata
            return ModelResult(
                text=text.strip(),
                model=self.settings.gemini_model,
                input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                output_tokens=(
                    (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
                )
                if usage
                else 0,
            )
        except ModelUnavailable:
            raise
        except Exception:
            # Provider errors may contain key-bearing URLs or request contents. Do not expose them.
            raise ModelUnavailable(
                "Gemini request failed; check model access, API key, quota, and connectivity"
            ) from None

    def close(self) -> None:
        self.client.close()
