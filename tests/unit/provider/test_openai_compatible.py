"""Unit tests for OpenAI-compatible provider."""

from unittest.mock import AsyncMock

import httpx
import pytest
from loguru import logger
from openai import APIStatusError
from openai.types.chat import ChatCompletion, ChatCompletionChunk, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import Choice as ChunkChoice
from pydantic import BaseModel, Field

from llm4ad.infra.provider.base import ChatMessage, GenerationResult
from llm4ad.infra.provider.openai_compatible import OpenAICompatibleProvider


@pytest.fixture
def provider_config():
    """Default provider configuration for testing."""
    return {
        "api_key": "test_key",
        "base_url": "http://localhost:8000/v1",
        "model": "test-model",
        "input_cost_per_million": 0.15,
        "output_cost_per_million": 0.60,
    }


@pytest.fixture
def provider(provider_config):
    """Create an OpenAICompatibleProvider instance with mocked client."""
    provider = OpenAICompatibleProvider(provider_config)
    provider.client = AsyncMock()
    return provider


def test_provider_initialization(provider_config):
    """Test provider initialization with custom config."""
    provider = OpenAICompatibleProvider(provider_config)

    assert provider.api_key == "test_key"
    assert provider.base_url == "http://localhost:8000/v1"
    assert provider.model == "test-model"
    assert provider.input_cost_per_million == 0.15
    assert provider.output_cost_per_million == 0.60


def test_estimate_cost(provider):
    """Test cost estimation functionality."""
    # 1M input tokens = $0.15, 1M output tokens = $0.60
    cost = provider.estimate_cost(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 0.75  # 0.15 + 0.60

    # 500k input, 200k output
    cost = provider.estimate_cost(input_tokens=500_000, output_tokens=200_000)
    assert cost == 0.075 + 0.12 == 0.195


def test_get_model_info(provider):
    """Test get_model_info returns correct information."""
    info = provider.get_model_info()

    assert info["name"] == "test-model"
    assert info["provider"] == "openai_compatible"
    assert info["base_url"] == "http://localhost:8000/v1"
    assert info["input_cost_per_million"] == 0.15
    assert info["output_cost_per_million"] == 0.60


@pytest.mark.asyncio
async def test_chat_success(provider):
    """Test successful chat completion."""
    # Mock response
    mock_response = ChatCompletion(
        id="test-id-123",
        created=1234567890,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(role="assistant", content="This is a test response"),
            )
        ],
        usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        object="chat.completion",
        system_fingerprint="test-fingerprint",
    )

    provider.client.chat.completions.create.return_value = mock_response

    # Test messages
    messages = [
        ChatMessage(role="system", content="You are a helpful assistant"),
        ChatMessage(role="user", content="Hello, how are you?"),
    ]

    result = await provider.chat(messages, temperature=0.7, max_tokens=100)

    # Verify result
    assert isinstance(result, GenerationResult)
    assert result.text == "This is a test response"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 20
    assert result.total_tokens == 30
    assert result.cost_usd == (10 / 1e6) * 0.15 + (20 / 1e6) * 0.60 == pytest.approx(0.0000135)
    assert result.model == "test-model"
    assert result.finish_reason == "stop"
    assert result.metadata["id"] == "test-id-123"
    assert result.metadata["created"] == 1234567890
    assert result.metadata["system_fingerprint"] == "test-fingerprint"

    # Verify client was called correctly
    provider.client.chat.completions.create.assert_called_once_with(
        model="test-model",
        messages=[
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Hello, how are you?"},
        ],
        stream=False,
        temperature=0.7,
        max_tokens=100,
    )


@pytest.mark.asyncio
async def test_chat_with_named_messages(provider):
    """Test chat messages with name field."""
    mock_response = ChatCompletion(
        id="test-id-456",
        created=1234567890,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(role="assistant", content="Hi Alice!"),
            )
        ],
        usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        object="chat.completion",
    )

    provider.client.chat.completions.create.return_value = mock_response

    messages = [
        ChatMessage(role="user", content="Hello, my name is Alice", name="Alice"),
    ]

    await provider.chat(messages)

    provider.client.chat.completions.create.assert_called_once_with(
        model="test-model",
        messages=[{"role": "user", "content": "Hello, my name is Alice", "name": "Alice"}],
        stream=False,
    )


@pytest.mark.asyncio
async def test_chat_without_usage(provider):
    """Test chat when response has no usage information."""
    mock_response = ChatCompletion(
        id="test-id-789",
        created=1234567890,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(role="assistant", content="Response without usage"),
            )
        ],
        usage=None,
        object="chat.completion",
    )

    provider.client.chat.completions.create.return_value = mock_response

    messages = [ChatMessage(role="user", content="Hello")]
    result = await provider.chat(messages)

    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0
    assert result.total_tokens == 0
    assert result.cost_usd == 0.0


@pytest.mark.asyncio
async def test_generate(provider):
    """Test generate (single prompt) functionality."""
    # Mock chat response (generate delegates to chat)
    mock_response = ChatCompletion(
        id="gen-id-123",
        created=1234567890,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(role="assistant", content="Generated text"),
            )
        ],
        usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        object="chat.completion",
    )

    provider.client.chat.completions.create.return_value = mock_response

    result = await provider.generate("Write a sentence", temperature=0.5)

    assert result.text == "Generated text"
    provider.client.chat.completions.create.assert_called_once_with(
        model="test-model",
        messages=[{"role": "user", "content": "Write a sentence"}],
        stream=False,
        temperature=0.5,
    )


@pytest.mark.asyncio
async def test_chat_stream(provider):
    """Test streaming chat completion."""
    # Create mock chunks
    chunks = [
        ChatCompletionChunk(
            id="stream-123",
            created=1234567890,
            model="test-model",
            choices=[ChunkChoice(delta={"content": "Hello"}, index=0, finish_reason=None)],
            object="chat.completion.chunk",
        ),
        ChatCompletionChunk(
            id="stream-123",
            created=1234567890,
            model="test-model",
            choices=[ChunkChoice(delta={"content": " world"}, index=0, finish_reason=None)],
            object="chat.completion.chunk",
        ),
        ChatCompletionChunk(
            id="stream-123",
            created=1234567890,
            model="test-model",
            choices=[ChunkChoice(delta={}, index=0, finish_reason="stop")],
            object="chat.completion.chunk",
        ),
    ]

    # Mock async stream — create() must return a coroutine that resolves to an
    # async iterable, matching the new StreamResponse-based contract.
    async def mock_stream(*args, **kwargs):
        for chunk in chunks:
            yield chunk

    async def mock_create(*args, **kwargs):
        return mock_stream()

    provider.client.chat.completions.create = mock_create

    # Collect stream chunks
    collected = []
    stream = await provider.chat_stream([ChatMessage(role="user", content="Hi")])
    async for chunk in stream:
        collected.append(chunk)

    assert collected == ["Hello", " world"]


@pytest.mark.asyncio
async def test_generate_stream(provider):
    """Test streaming generate functionality."""
    # Mock stream response (generate_stream delegates to chat_stream)
    chunks = [
        ChatCompletionChunk(
            id="stream-gen-123",
            created=1234567890,
            model="test-model",
            choices=[ChunkChoice(delta={"content": "Test"}, index=0, finish_reason=None)],
            object="chat.completion.chunk",
        ),
        ChatCompletionChunk(
            id="stream-gen-123",
            created=1234567890,
            model="test-model",
            choices=[ChunkChoice(delta={"content": " stream"}, index=0, finish_reason=None)],
            object="chat.completion.chunk",
        ),
    ]

    async def mock_stream(*args, **kwargs):
        for chunk in chunks:
            yield chunk

    async def mock_create(*args, **kwargs):
        return mock_stream()

    provider.client.chat.completions.create = mock_create

    collected = []
    async for chunk in provider.generate_stream("Generate something"):
        collected.append(chunk)

    assert collected == ["Test", " stream"]


@pytest.mark.asyncio
async def test_chat_with_schema_success(provider):
    """Test chat with schema parses response correctly."""

    class PersonInfo(BaseModel):
        name: str
        age: int

    mock_response = ChatCompletion(
        id="schema-id-123",
        created=1234567890,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant", content='{"name": "Alice", "age": 30}'
                ),
            )
        ],
        usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        object="chat.completion",
    )

    provider.client.chat.completions.create.return_value = mock_response

    messages = [ChatMessage(role="user", content="Tell me about a person")]
    result = await provider.chat(messages, schema=PersonInfo)

    assert isinstance(result, GenerationResult)
    assert result.parsed is not None
    assert result.parsed.name == "Alice"
    assert result.parsed.age == 30


@pytest.mark.asyncio
async def test_chat_retry_log_interpolates_api_status_error(provider, monkeypatch):
    """Retry logs should render status, wait, attempt, and error details."""

    class Reply(BaseModel):
        answer: str

    mock_response = ChatCompletion(
        id="retry-id-123",
        created=1234567890,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(role="assistant", content='{"answer": "ok"}'),
            )
        ],
        usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        object="chat.completion",
    )
    status_error = APIStatusError(
        "service unavailable",
        response=httpx.Response(
            status_code=503,
            request=httpx.Request("POST", "http://localhost:8000/v1/chat/completions"),
        ),
        body={"error": "temporary overload"},
    )
    provider.client.chat.completions.create.side_effect = [status_error, mock_response]
    monkeypatch.setattr("llm4ad.infra.provider.openai_compatible.asyncio.sleep", AsyncMock())

    records = []
    sink_id = logger.add(lambda message: records.append(message.record), level="WARNING")
    try:
        result = await provider.generate("Return JSON", schema=Reply)
    finally:
        logger.remove(sink_id)

    assert result.parsed.answer == "ok"
    messages = [record["message"] for record in records]
    assert any("API returned 503, retrying in 1s (attempt 1/3)" in message for message in messages)
    assert all("%d" not in message and "%s" not in message for message in messages)


@pytest.mark.asyncio
async def test_chat_with_schema_enables_json_mode_for_deepseek(provider_config):
    """DeepSeek schema calls should use JSON mode to reduce malformed output."""

    class PersonInfo(BaseModel):
        name: str = Field(..., description="Concise name for the algorithm")
        age: int

    deepseek_provider = OpenAICompatibleProvider({
        **provider_config,
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    })
    deepseek_provider.client = AsyncMock()
    deepseek_provider.client.chat.completions.create.return_value = ChatCompletion(
        id="schema-id-deepseek",
        created=1234567890,
        model="deepseek-chat",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant", content='{"name": "Alice", "age": 30}'
                ),
            )
        ],
        usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        object="chat.completion",
    )

    messages = [ChatMessage(role="user", content="Tell me about a person")]
    result = await deepseek_provider.chat(messages, schema=PersonInfo)

    assert result.parsed is not None
    call_kwargs = deepseek_provider.client.chat.completions.create.call_args.kwargs
    assert call_kwargs["response_format"] == {"type": "json_object"}
    assert "Do not return a JSON Schema" in call_kwargs["messages"][0]["content"]
    assert "Example JSON output" in call_kwargs["messages"][0]["content"]
    assert '"name": "Concise name for the algorithm"' in call_kwargs["messages"][0]["content"]
    assert "Do not output keys named" in call_kwargs["messages"][0]["content"]


@pytest.mark.asyncio
async def test_chat_with_schema_enables_json_mode_for_openai_type(provider_config):
    """OpenAI providers should request JSON output for schema calls."""

    class PersonInfo(BaseModel):
        name: str
        age: int

    openai_provider = OpenAICompatibleProvider({
        **provider_config,
        "type": "openai",
        "base_url": "https://gateway.example/v1",
        "model": "gateway-model-alias",
    })
    openai_provider.client = AsyncMock()
    openai_provider.client.chat.completions.create.return_value = ChatCompletion(
        id="schema-id-openai",
        created=1234567890,
        model="gateway-model-alias",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant", content='{"name": "Alice", "age": 30}'
                ),
            )
        ],
        usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        object="chat.completion",
    )

    result = await openai_provider.chat(
        [ChatMessage(role="user", content="Tell me about a person")],
        schema=PersonInfo,
    )

    assert result.parsed is not None
    call_kwargs = openai_provider.client.chat.completions.create.call_args.kwargs
    assert call_kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_chat_with_schema_downgrades_when_response_format_is_unsupported(provider_config):
    """An explicit response-format rejection should retry in prompt-only mode."""

    class PersonInfo(BaseModel):
        name: str
        age: int

    openai_provider = OpenAICompatibleProvider({
        **provider_config,
        "type": "openai",
        "base_url": "https://gateway.example/v1",
    })
    openai_provider.client = AsyncMock()
    unsupported_error = APIStatusError(
        "Unsupported parameter: response_format",
        response=httpx.Response(
            status_code=400,
            request=httpx.Request("POST", "https://gateway.example/v1/chat/completions"),
        ),
        body={"error": {"message": "response_format is not supported"}},
    )
    valid_response = ChatCompletion(
        id="schema-fallback",
        created=1234567890,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant", content='{"name": "Alice", "age": 30}'
                ),
            )
        ],
        usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        object="chat.completion",
    )
    openai_provider.client.chat.completions.create.side_effect = [
        unsupported_error,
        valid_response,
    ]

    result = await openai_provider.chat(
        [ChatMessage(role="user", content="Tell me about a person")],
        schema=PersonInfo,
    )

    assert result.parsed is not None
    calls = openai_provider.client.chat.completions.create.call_args_list
    assert calls[0].kwargs["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1].kwargs


@pytest.mark.asyncio
async def test_chat_with_schema_retry_identifies_schema_echo_for_deepseek(provider_config):
    """When DeepSeek echoes a JSON Schema, retry feedback should ask for an instance."""

    class PersonInfo(BaseModel):
        name: str
        age: int

    deepseek_provider = OpenAICompatibleProvider({
        **provider_config,
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    })
    deepseek_provider.client = AsyncMock()
    schema_echo = ChatCompletion(
        id="schema-echo-1",
        created=1234567890,
        model="deepseek-chat",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant",
                    content='{"properties": {"name": {"type": "string"}}, "type": "object"}',
                ),
            )
        ],
        usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        object="chat.completion",
    )
    valid_response = ChatCompletion(
        id="schema-echo-2",
        created=1234567891,
        model="deepseek-chat",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant", content='{"name": "Alice", "age": 30}'
                ),
            )
        ],
        usage={"prompt_tokens": 60, "completion_tokens": 20, "total_tokens": 80},
        object="chat.completion",
    )
    deepseek_provider.client.chat.completions.create.side_effect = [schema_echo, valid_response]

    result = await deepseek_provider.chat(
        [ChatMessage(role="user", content="Tell me about a person")],
        schema=PersonInfo,
    )

    assert result.parsed is not None
    second_call_kwargs = deepseek_provider.client.chat.completions.create.call_args_list[1].kwargs
    assert "returned a JSON Schema instead of a JSON instance" in second_call_kwargs["messages"][0]["content"]


@pytest.mark.asyncio
async def test_chat_with_schema_does_not_enable_json_mode_for_other_compatible_models(provider):
    """Generic OpenAI-compatible endpoints should keep the existing request shape."""

    class PersonInfo(BaseModel):
        name: str
        age: int

    provider.client.chat.completions.create.return_value = ChatCompletion(
        id="schema-id-generic",
        created=1234567890,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant", content='{"name": "Alice", "age": 30}'
                ),
            )
        ],
        usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        object="chat.completion",
    )

    await provider.chat([ChatMessage(role="user", content="Tell me about a person")], schema=PersonInfo)

    call_kwargs = provider.client.chat.completions.create.call_args.kwargs
    assert "response_format" not in call_kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("json_mode", "base_url", "model", "expected"),
    [
        ("force", "http://localhost:8000/v1", "local-model", True),
        ("off", "https://api.deepseek.com", "deepseek-chat", False),
    ],
)
async def test_chat_with_schema_json_mode_config_override(
    provider_config, json_mode, base_url, model, expected
):
    """Provider config can force or disable JSON mode detection."""

    class PersonInfo(BaseModel):
        name: str
        age: int

    provider = OpenAICompatibleProvider({
        **provider_config,
        "base_url": base_url,
        "model": model,
        "json_mode": json_mode,
    })
    provider.client = AsyncMock()
    provider.client.chat.completions.create.return_value = ChatCompletion(
        id="schema-id-json-mode",
        created=1234567890,
        model=model,
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant", content='{"name": "Alice", "age": 30}'
                ),
            )
        ],
        usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        object="chat.completion",
    )

    await provider.chat([ChatMessage(role="user", content="Tell me about a person")], schema=PersonInfo)

    call_kwargs = provider.client.chat.completions.create.call_args.kwargs
    assert ("response_format" in call_kwargs) is expected


@pytest.mark.asyncio
async def test_chat_with_schema_json_in_markdown(provider):
    """Test chat with schema parses JSON from markdown code blocks."""

    class PersonInfo(BaseModel):
        name: str
        age: int

    mock_response = ChatCompletion(
        id="schema-md-123",
        created=1234567890,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant",
                    content='```json\n{"name": "Bob", "age": 25}\n```',
                ),
            )
        ],
        usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        object="chat.completion",
    )

    provider.client.chat.completions.create.return_value = mock_response

    messages = [ChatMessage(role="user", content="Tell me about a person")]
    result = await provider.chat(messages, schema=PersonInfo)

    assert result.parsed is not None
    assert result.parsed.name == "Bob"
    assert result.parsed.age == 25


@pytest.mark.asyncio
async def test_chat_with_schema_extracts_json_wrapped_in_prose(provider):
    """A complete schema-valid JSON object may be wrapped in brief prose."""

    class PersonInfo(BaseModel):
        name: str
        age: int

    provider.client.chat.completions.create.return_value = ChatCompletion(
        id="schema-prose-123",
        created=1234567890,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant",
                    content='Here is the result:\n{"name": "Bob", "age": 25}\nDone.',
                ),
            )
        ],
        usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        object="chat.completion",
    )

    result = await provider.chat(
        [ChatMessage(role="user", content="Tell me about a person")],
        schema=PersonInfo,
    )

    assert result.parsed is not None
    assert result.parsed.name == "Bob"
    assert result.parsed.age == 25


@pytest.mark.asyncio
async def test_chat_with_schema_logs_bounded_parse_diagnostics(provider):
    """Parse warnings should classify responses without dumping full content."""

    class PersonInfo(BaseModel):
        name: str
        age: int

    invalid_response = ChatCompletion(
        id="schema-invalid-123",
        created=1234567890,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant", content="not-json " + "x" * 300
                ),
            )
        ],
        usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        object="chat.completion",
    )
    valid_response = ChatCompletion(
        id="schema-valid-123",
        created=1234567891,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant", content='{"name": "Alice", "age": 30}'
                ),
            )
        ],
        usage={"prompt_tokens": 60, "completion_tokens": 20, "total_tokens": 80},
        object="chat.completion",
    )
    provider.client.chat.completions.create.side_effect = [invalid_response, valid_response]

    records = []
    sink_id = logger.add(lambda message: records.append(message.record), level="WARNING")
    try:
        result = await provider.chat(
            [ChatMessage(role="user", content="Tell me about a person")],
            schema=PersonInfo,
        )
    finally:
        logger.remove(sink_id)

    assert result.parsed is not None
    warning = next(
        record["message"] for record in records
        if "Failed to parse schema" in record["message"]
    )
    assert "category=malformed_json" in warning
    assert "finish_reason=stop" in warning
    assert "content_length=309" in warning
    assert "preview='not-json" in warning
    assert len(warning) < 500


@pytest.mark.asyncio
async def test_chat_with_schema_retry_on_parse_error(provider):
    """Test chat with schema retries when JSON parsing fails."""

    class PersonInfo(BaseModel):
        name: str
        age: int
        city: str

    # First response has invalid JSON
    mock_response_1 = ChatCompletion(
        id="retry-1",
        created=1234567890,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant", content='{"name": "Alice", "age": }'
                ),
            )
        ],
        usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        object="chat.completion",
    )

    # Second response has valid JSON
    mock_response_2 = ChatCompletion(
        id="retry-2",
        created=1234567891,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant",
                    content='{"name": "Alice", "age": 30, "city": "Beijing"}',
                ),
            )
        ],
        usage={"prompt_tokens": 80, "completion_tokens": 25, "total_tokens": 105},
        object="chat.completion",
    )

    # Mock the API to return first invalid, then valid response
    provider.client.chat.completions.create.side_effect = [
        mock_response_1,
        mock_response_2,
    ]

    messages = [ChatMessage(role="user", content="Tell me about a person")]
    result = await provider.chat(messages, schema=PersonInfo)

    # Should succeed after retry
    assert result.parsed is not None
    assert result.parsed.name == "Alice"
    assert result.parsed.age == 30
    assert result.parsed.city == "Beijing"

    # Should have called API twice
    assert provider.client.chat.completions.create.call_count == 2


@pytest.mark.asyncio
async def test_chat_with_schema_retry_exhausted(provider):
    """Test chat with schema returns None when all retries exhausted."""

    class PersonInfo(BaseModel):
        name: str
        age: int

    # Always return invalid JSON
    mock_response = ChatCompletion(
        id="fail-123",
        created=1234567890,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant", content='{"name": "Alice", "age": }'
                ),
            )
        ],
        usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        object="chat.completion",
    )

    provider.client.chat.completions.create.return_value = mock_response

    messages = [ChatMessage(role="user", content="Tell me about a person")]
    result = await provider.chat(messages, schema=PersonInfo)

    # Should return result with parsed=None after retries exhausted
    assert result.parsed is None
    # Should have called API 3 times (initial + 2 retries)
    assert provider.client.chat.completions.create.call_count == 3


@pytest.mark.asyncio
async def test_chat_with_list_schema(provider):
    """Test chat with schema that returns a list."""

    class Item(BaseModel):
        id: int
        name: str

    class ItemsList(BaseModel):
        items: list[Item]
        total: int

    mock_response = ChatCompletion(
        id="list-schema-123",
        created=1234567890,
        model="test-model",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    role="assistant",
                    content='{"items": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}], "total": 2}',
                ),
            )
        ],
        usage={"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
        object="chat.completion",
    )

    provider.client.chat.completions.create.return_value = mock_response

    messages = [ChatMessage(role="user", content="List items")]
    result = await provider.chat(messages, schema=ItemsList)

    assert result.parsed is not None
    assert result.parsed.total == 2
    assert len(result.parsed.items) == 2
    assert result.parsed.items[0].name == "A"
    assert result.parsed.items[1].name == "B"
