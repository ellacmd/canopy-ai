"""Server-only Gemini adapter. No financial or database actions are exposed to it."""

from dataclasses import dataclass
from typing import Protocol

from google import genai
from google.genai import types
from pydantic import BaseModel

from canopy.config import Settings


class ModelUnavailable(RuntimeError):
    def __init__(
        self, message, *, code="unavailable", input_tokens=0, output_tokens=0, usage_complete=False
    ):
        super().__init__(message)
        self.code = code
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.usage_complete = usage_complete


@dataclass(frozen=True)
class ModelResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    usage_complete: bool = True


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
        return self._generate(prompt, system=system)

    def generate_structured(
        self, prompt: str, *, system: str, schema: type[BaseModel]
    ) -> ModelResult:
        return self._generate(prompt, system=system, schema=schema)

    def _generate(self, prompt: str, *, system: str, schema=None) -> ModelResult:
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
                    **(
                        {
                            "response_mime_type": "application/json",
                            "response_json_schema": schema.model_json_schema(),
                            "thinking_config": types.ThinkingConfig(
                                thinking_level=types.ThinkingLevel.MINIMAL
                            ),
                        }
                        if schema
                        else {}
                    ),
                ),
            )
            usage = response.usage_metadata
            input_tokens = (usage.prompt_token_count or 0) if usage else 0
            output_tokens = (
                ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0))
                if usage
                else 0
            )
            usage_complete = bool(
                usage is not None
                and usage.prompt_token_count is not None
                and usage.candidates_token_count is not None
            )
            candidates = response.candidates or []
            if not candidates or str(candidates[0].finish_reason) not in {
                "STOP",
                "FinishReason.STOP",
            }:
                reason = str(candidates[0].finish_reason) if candidates else ""
                raise ModelUnavailable(
                    "Gemini did not return a complete response",
                    code="truncated" if "MAX_TOKENS" in reason else "blocked",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    usage_complete=usage_complete,
                )
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
                usage_complete=bool(
                    usage is not None
                    and usage.prompt_token_count is not None
                    and usage.candidates_token_count is not None
                ),
            )
        except ModelUnavailable:
            raise
        except Exception as error:
            # Provider errors may contain key-bearing URLs or request contents. Do not expose them.
            raise ModelUnavailable(
                "Gemini request failed; check model access, API key, quota, and connectivity",
                code=(
                    "rate_limited"
                    if getattr(error, "code", None) == 429
                    else f"provider_http_{error.code}"
                    if getattr(error, "code", None) in {400, 401, 403, 404, 500, 502, 503, 504}
                    else "provider_" + type(error).__name__
                    if type(error).__name__
                    in {
                        "ReadTimeout",
                        "ConnectTimeout",
                        "ConnectError",
                        "RemoteProtocolError",
                        "JSONDecodeError",
                        "AttributeError",
                        "TypeError",
                        "ValidationError",
                    }
                    else "provider_unavailable"
                ),
            ) from None

    def close(self) -> None:
        self.client.close()
