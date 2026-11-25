from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from canopy.config import Settings
from canopy.llm import GeminiModel, ModelUnavailable


def response(*, text="canopy-ready", reason="STOP", candidates=True):
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason=reason)] if candidates else [],
        usage_metadata=SimpleNamespace(
            prompt_token_count=20,
            candidates_token_count=3,
            thoughts_token_count=5,
        ),
    )


def test_gemini_configuration_limits_and_billed_output(settings):
    client = Mock()
    client.models.generate_content.return_value = response()
    adapter = GeminiModel(settings, client=client)
    result = adapter.generate("Synthetic input", system="Connectivity test only")
    assert result.text == "canopy-ready"
    assert result.input_tokens == 20
    assert result.output_tokens == 8  # Includes thinking, when reported by the provider.
    kwargs = client.models.generate_content.call_args.kwargs
    assert kwargs["model"] == "gemini-3.5-flash-lite"
    assert kwargs["config"].max_output_tokens == 512
    assert kwargs["config"].automatic_function_calling.disable is True
    assert kwargs["config"].tools is None


def test_gemini_rejects_oversized_input_before_calling_provider(settings):
    client = Mock()
    adapter = GeminiModel(settings, client=client)
    with pytest.raises(ValueError, match="input limit"):
        adapter.generate("x" * settings.gemini_max_input_characters, system="system")
    client.models.generate_content.assert_not_called()


@pytest.mark.parametrize(
    "result",
    [
        response(text=""),
        response(reason="MAX_TOKENS"),
        response(reason="SAFETY"),
        response(candidates=False),
    ],
)
def test_gemini_refuses_empty_blocked_or_truncated_responses(settings, result):
    client = Mock()
    client.models.generate_content.return_value = result
    with pytest.raises(ModelUnavailable):
        GeminiModel(settings, client=client).generate("synthetic", system="test")


def test_gemini_provider_errors_do_not_expose_key_or_prompt(settings):
    client = Mock()
    client.models.generate_content.side_effect = RuntimeError("url?key=private-key secret-prompt")
    with pytest.raises(ModelUnavailable) as error:
        GeminiModel(settings, client=client).generate("secret-prompt", system="test")
    assert "private-key" not in str(error.value)
    assert "secret-prompt" not in str(error.value)
    assert client.models.generate_content.call_count == 1


def test_gemini_missing_key_fails_without_network(settings):
    with pytest.raises(ModelUnavailable, match="GEMINI_API_KEY"):
        GeminiModel(settings)


def test_structured_output_pins_schema_and_thinking_and_classifies_safe_errors(settings):
    from canopy.workflow_contracts import Classification

    client = Mock()
    client.models.generate_content.return_value = response(text='{"intent":"other"}')
    model = GeminiModel(settings, client=client)
    model.generate_structured("synthetic", system="Classify", schema=Classification)
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == Classification.model_json_schema()
    assert str(config.thinking_config.thinking_level).endswith("MINIMAL")
    client.models.generate_content.return_value = response(reason="MAX_TOKENS")
    with pytest.raises(ModelUnavailable) as error:
        model.generate_structured("synthetic", system="Classify", schema=Classification)
    assert error.value.code == "truncated" and error.value.output_tokens == 8
    failure = RuntimeError("private-key private-message")
    failure.code = 429
    client.models.generate_content.side_effect = failure
    with pytest.raises(ModelUnavailable) as error:
        model.generate("synthetic", system="test")
    assert error.value.code == "rate_limited" and "private" not in str(error.value)


def test_real_sdk_client_uses_timeout_and_no_implicit_retries(settings, monkeypatch):
    constructor = Mock()
    monkeypatch.setattr("canopy.llm.genai.Client", constructor)
    keyed = Settings.model_validate(
        {
            **settings.model_dump(),
            "gemini_api_key": "synthetic-test-key",
        }
    )
    adapter = GeminiModel(keyed)
    options = constructor.call_args.kwargs["http_options"]
    assert options.timeout == 20000
    assert options.retry_options.attempts == 1
    adapter.close()
    constructor.return_value.close.assert_called_once()
