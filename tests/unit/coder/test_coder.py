"""Unit tests for llm4ad.coder module."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm4ad.coder.base import (
    BaseCoder,
    CodingAgentType,
    GenerateResult,
    GenerateStatus,
)
from llm4ad.config.app import ProviderConfig
from llm4ad.config.schema import ClaudeCodeConfig, CustomCoderConfig
from llm4ad.infra.provider.base import GenerationResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def custom_config():
    """Default CustomCoderConfig for testing."""
    return CustomCoderConfig(
        type="custom",
        temperature=0.5,
        max_gen_tokens=2048,
        default_extension="py",
        context_max_tokens=1024,
    )


@pytest.fixture
def mock_provider():
    """Mock LLM provider with async generate."""
    provider = MagicMock()
    provider.generate = AsyncMock()
    return provider


@pytest.fixture
def coder(custom_config, mock_provider):
    """Create a CustomNaiveCoder with mocked provider."""
    from llm4ad.coder.custom_naive_coder import CustomNaiveCoder

    return CustomNaiveCoder(config=custom_config, provider=mock_provider)


# ---------------------------------------------------------------------------
# GenerateStatus
# ---------------------------------------------------------------------------


class TestGenerateStatus:
    """Tests for GenerateStatus enum."""

    def test_status_values(self):
        assert GenerateStatus.SUCCESS.value == "success"
        assert GenerateStatus.FAILED.value == "failed"
        assert GenerateStatus.TIMEOUT.value == "timeout"
        assert GenerateStatus.PARTIAL.value == "partial"


# ---------------------------------------------------------------------------
# GenerateResult
# ---------------------------------------------------------------------------


class TestGenerateResult:
    """Tests for GenerateResult model."""

    def test_default_construction(self):
        result = GenerateResult(status=GenerateStatus.SUCCESS, working_dir="/tmp/work")
        assert result.status == GenerateStatus.SUCCESS
        assert result.working_dir == "/tmp/work"
        assert result.generated_files == []
        assert result.main_file is None
        assert result.error_message is None
        assert result.metadata == {}
        assert result.token_used == 0

    def test_full_construction(self):
        result = GenerateResult(
            status=GenerateStatus.PARTIAL,
            working_dir="/tmp/work",
            generated_files=["main.py", "utils.py"],
            main_file="main.py",
            error_message="utils.py had warnings",
            metadata={"tokens_used": 100},
            token_used=100,
        )
        assert result.generated_files == ["main.py", "utils.py"]
        assert result.main_file == "main.py"
        assert result.error_message == "utils.py had warnings"
        assert result.metadata["tokens_used"] == 100
        assert result.token_used == 100

    def test_is_success(self):
        success = GenerateResult(status=GenerateStatus.SUCCESS, working_dir="/tmp")
        failed = GenerateResult(status=GenerateStatus.FAILED, working_dir="/tmp")
        partial = GenerateResult(status=GenerateStatus.PARTIAL, working_dir="/tmp")
        assert success.is_success is True
        assert failed.is_success is False
        assert partial.is_success is False

    def test_is_partial(self):
        partial = GenerateResult(status=GenerateStatus.PARTIAL, working_dir="/tmp")
        success = GenerateResult(status=GenerateStatus.SUCCESS, working_dir="/tmp")
        assert partial.is_partial is True
        assert success.is_partial is False

    def test_get_absolute_paths(self):
        result = GenerateResult(
            status=GenerateStatus.SUCCESS,
            working_dir="/tmp/work",
            generated_files=["src/main.py", "src/utils.py"],
        )
        paths = result.get_absolute_paths()
        assert len(paths) == 2
        assert paths[0] == str(Path("/tmp/work/src/main.py"))
        assert paths[1] == str(Path("/tmp/work/src/utils.py"))

    def test_get_absolute_paths_empty(self):
        result = GenerateResult(status=GenerateStatus.FAILED, working_dir="/tmp/work")
        assert result.get_absolute_paths() == []


# ---------------------------------------------------------------------------
# CodingAgentType
# ---------------------------------------------------------------------------


class TestCodingAgentType:
    """Tests for CodingAgentType enum."""

    def test_values(self):
        assert CodingAgentType.CLAUDE_CODE.value == "claude_code"
        assert CodingAgentType.CUSTOM.value == "custom"
        assert CodingAgentType.OPENCODE.value == "opencode"


# ---------------------------------------------------------------------------
# BaseCoder registry
# ---------------------------------------------------------------------------


class TestBaseCoderRegistry:
    """Tests for BaseCoder registration and discovery."""

    def test_custom_registered(self):
        from llm4ad.coder.custom_naive_coder import CustomNaiveCoder

        cls = BaseCoder.get("custom")
        assert cls is CustomNaiveCoder

    def test_claude_code_registered(self):
        from llm4ad.coder.claude_code import ClaudeCode

        cls = BaseCoder.get("claude_code")
        assert cls is ClaudeCode

    def test_list_includes_registered(self):
        names = BaseCoder.list()
        assert "custom" in names
        assert "claude_code" in names

    def test_create_custom(self, custom_config, mock_provider):
        from llm4ad.coder.custom_naive_coder import CustomNaiveCoder

        instance = BaseCoder.create("custom", config=custom_config, provider=mock_provider)
        assert isinstance(instance, CustomNaiveCoder)

    def test_prepare_context(self, coder):
        """Test the concrete prepare_context method on BaseCoder."""
        base = {"task_description": "sort integers"}
        result = coder.prepare_context("use quicksort", base)
        assert result["insight"] == "use quicksort"
        assert result["task_description"] == "sort integers"
        # Original dict should not be mutated
        assert "insight" not in base


# ---------------------------------------------------------------------------
# CustomNaiveCoder - init & properties
# ---------------------------------------------------------------------------


class TestCustomNaiveCoderInit:
    """Tests for CustomNaiveCoder initialization."""

    def test_name_property(self, coder):
        assert coder.name == "custom"

    def test_config_stored(self, coder, custom_config):
        assert coder.temperature == custom_config.temperature
        assert coder.max_tokens == custom_config.max_gen_tokens
        assert coder.default_extension == custom_config.default_extension

    def test_agent_type(self, coder):
        assert coder.agent_type == CodingAgentType.CUSTOM


# ---------------------------------------------------------------------------
# CustomNaiveCoder.validate_code
# ---------------------------------------------------------------------------


class TestValidateCode:
    """Tests for CustomNaiveCoder.validate_code."""

    def test_valid_python(self, coder):
        is_valid, error = coder.validate_code("x = 1\nprint(x)", "python")
        assert is_valid is True
        assert error is None

    def test_invalid_python_syntax(self, coder):
        is_valid, error = coder.validate_code("def f(\n", "python")
        assert is_valid is False
        assert "syntax error" in error.lower()

    def test_empty_code(self, coder):
        is_valid, error = coder.validate_code("", "python")
        assert is_valid is False
        assert "empty" in error.lower()

    def test_whitespace_only(self, coder):
        is_valid, error = coder.validate_code("   \n  ", "python")
        assert is_valid is False

    def test_non_python_valid(self, coder):
        is_valid, error = coder.validate_code("int main() { return 0; }", "cpp")
        assert is_valid is True
        assert error is None

    def test_non_python_empty(self, coder):
        is_valid, error = coder.validate_code("", "cpp")
        assert is_valid is False


# ---------------------------------------------------------------------------
# CustomNaiveCoder._extract_code
# ---------------------------------------------------------------------------


class TestExtractCode:
    """Tests for CustomNaiveCoder._extract_code."""

    def test_extract_from_markdown_block(self, coder):
        text = "Here is the code:\n```python\nprint('hello')\n```\nDone."
        result = coder._extract_code(text)
        assert result == "print('hello')"

    def test_extract_fallback_raw_text(self, coder):
        text = "print('hello')"
        result = coder._extract_code(text)
        assert result == "print('hello')"

    def test_extract_empty_returns_none(self, coder):
        result = coder._extract_code("")
        assert result is None

    def test_extract_whitespace_only_returns_none(self, coder):
        result = coder._extract_code("   \n  ")
        assert result is None


# ---------------------------------------------------------------------------
# CustomNaiveCoder._extract_multi_file_code
# ---------------------------------------------------------------------------


class TestExtractMultiFileCode:
    """Tests for CustomNaiveCoder._extract_multi_file_code."""

    def test_named_blocks(self, coder):
        text = (
            "```python:src/main.py\nprint('main')\n```\n"
            "```python:src/utils.py\ndef helper(): pass\n```"
        )
        files = coder._extract_multi_file_code(text)
        assert "src/main.py" in files
        assert "src/utils.py" in files
        assert files["src/main.py"] == "print('main')"
        assert files["src/utils.py"] == "def helper(): pass"

    def test_fallback_single_block(self, coder):
        text = "```python\nprint('hello')\n```"
        files = coder._extract_multi_file_code(text)
        assert len(files) == 1
        assert "implementation.py" in files
        assert files["implementation.py"] == "print('hello')"

    def test_empty_response(self, coder):
        files = coder._extract_multi_file_code("")
        assert files == {}


# ---------------------------------------------------------------------------
# CustomNaiveCoder._find_evolve_blocks
# ---------------------------------------------------------------------------


class TestFindEvolveBlocks:
    """Tests for EVOLVE block detection across comment styles."""

    def test_python_style(self, coder):
        content = "# EVOLVE START sorting\nx = 1\n# EVOLVE END"
        blocks = coder._find_evolve_blocks(content)
        assert len(blocks) == 1
        _pattern, comment_style, start_match, block_content = blocks[0]
        assert comment_style == "#"
        assert "sorting" in start_match
        assert "x = 1" in block_content

    def test_cpp_style(self, coder):
        content = "// EVOLVE START\nint x = 0;\n// EVOLVE END"
        blocks = coder._find_evolve_blocks(content)
        assert len(blocks) == 1
        assert blocks[0][1] == "//"

    def test_c_block_comment_style(self, coder):
        content = "/* EVOLVE START */\nint x = 0;\n/* EVOLVE END */"
        blocks = coder._find_evolve_blocks(content)
        assert len(blocks) == 1
        assert blocks[0][1] == "/* */"

    def test_html_style(self, coder):
        content = "<!-- EVOLVE START -->\n<div>hi</div>\n<!-- EVOLVE END -->"
        blocks = coder._find_evolve_blocks(content)
        assert len(blocks) == 1
        assert blocks[0][1] == "<!-- -->"

    def test_no_evolve_blocks(self, coder):
        content = "def hello():\n    pass\n"
        blocks = coder._find_evolve_blocks(content)
        assert blocks == []

    def test_multiple_blocks(self, coder):
        content = (
            "# EVOLVE START a\nblock_a\n# EVOLVE END\n"
            "some_code\n"
            "# EVOLVE START b\nblock_b\n# EVOLVE END"
        )
        blocks = coder._find_evolve_blocks(content)
        assert len(blocks) == 2


# ---------------------------------------------------------------------------
# CustomNaiveCoder._get_parent_code_map
# ---------------------------------------------------------------------------


class TestGetParentCodeMap:
    """Tests for parent code conversion."""

    def test_dict_input(self, coder):
        parent = {"main.py": "print(1)", "utils.py": "x = 2"}
        result = coder._get_parent_code_map(parent)
        assert result == parent

    def test_algorithm_input(self, coder):
        from llm4ad.planner.base import Algorithm, CodeArtifact, InsightType

        algo = Algorithm(
            insight_type=InsightType.INITIAL,
            description="test",
            code_artifacts=[
                CodeArtifact(file_path="main.py", content="print(1)"),
                CodeArtifact(file_path="utils.py", content="x = 2"),
            ],
        )
        result = coder._get_parent_code_map(algo)
        assert result == {"main.py": "print(1)", "utils.py": "x = 2"}

    def test_list_code_artifact_input(self, coder):
        from llm4ad.planner.base import CodeArtifact

        artifacts = [
            CodeArtifact(file_path="a.py", content="a"),
            CodeArtifact(file_path="b.py", content="b"),
        ]
        result = coder._get_parent_code_map(artifacts)
        assert result == {"a.py": "a", "b.py": "b"}

    def test_string_fallback(self, coder):
        result = coder._get_parent_code_map("print(1)")
        assert "algorithm.py" in result
        assert result["algorithm.py"] == "print(1)"


# ---------------------------------------------------------------------------
# CustomNaiveCoder._collect_project_context
# ---------------------------------------------------------------------------


class TestCollectProjectContext:
    """Tests for project context collection."""

    def test_collect_from_dir(self, coder, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")
        (tmp_path / "utils.py").write_text("x = 1", encoding="utf-8")
        context = coder._collect_project_context(str(tmp_path))
        assert "main.py" in context
        assert "utils.py" in context
        assert "print('hello')" in context

    def test_respects_max_chars(self, coder, tmp_path):
        (tmp_path / "big.py").write_text("x = 1\n" * 1000, encoding="utf-8")
        context = coder._collect_project_context(str(tmp_path), max_chars=100)
        # Should either be truncated or empty depending on overhead
        assert len(context) <= 200  # some overhead allowed

    def test_skips_non_source_files(self, coder, tmp_path):
        (tmp_path / "data.txt").write_text("not code", encoding="utf-8")
        (tmp_path / "image.png").write_bytes(b"\x89PNG")
        context = coder._collect_project_context(str(tmp_path))
        assert context == ""

    def test_empty_dir(self, coder, tmp_path):
        context = coder._collect_project_context(str(tmp_path))
        assert context == ""

    def test_nonexistent_dir(self, coder):
        context = coder._collect_project_context("/nonexistent/path")
        assert context == ""


# ---------------------------------------------------------------------------
# CustomNaiveCoder._replace_block_fuzzy
# ---------------------------------------------------------------------------


class TestReplaceBlockFuzzy:
    """Tests for fuzzy EVOLVE block replacement."""

    def test_fuzzy_replace_unsupported_style_returns_original(self, coder):
        """Block comment styles (/* */, <!-- -->) are not handled by fuzzy replace."""
        content = "/* EVOLVE START */\nold_code\n/* EVOLVE END */\n"
        result = coder._replace_block_fuzzy(
            content, "old_code", "new_code", "/* */", ""
        )
        # Fuzzy replace only supports # and // styles, so returns original
        assert result == content

    def test_comment_pattern_detection(self, coder):
        """Test the helper methods used by fuzzy replace."""
        assert coder._comment_pattern("#", "# EVOLVE START sorting")
        assert coder._comment_end_pattern("#", "# EVOLVE END")
        assert not coder._comment_pattern("#", "# just a comment")
        assert not coder._comment_end_pattern("#", "# just a comment")


# ---------------------------------------------------------------------------
# CustomNaiveCoder.generate - initial mode
# ---------------------------------------------------------------------------


class TestGenerateInitial:
    """Tests for initial code generation (no parent_code)."""

    @pytest.mark.asyncio
    async def test_custom_template_uses_structured_insight_from_context(
        self,
        mock_provider,
        tmp_path,
    ):
        """A custom template receives the raw insight, not a prebuilt fallback prompt."""
        from llm4ad.coder.custom_naive_coder import CustomNaiveCoder

        config = CustomCoderConfig(
            type="custom",
            prompt_template="STRICT CONTRACT\n{insight}\n{project_context}",
        )
        coder = CustomNaiveCoder(config=config, provider=mock_provider)
        mock_provider.generate.return_value = GenerationResult(
            text="```python:model_spec.py\nMODEL_SPEC = {}\n```",
            total_tokens=10,
        )

        await coder.generate(
            prompt="PREBUILT FALLBACK PROMPT",
            context={"insight": "RAW STRUCTURED INSIGHT", "language": "python"},
            working_dir=str(tmp_path),
        )

        sent_prompt = mock_provider.generate.await_args.args[0]
        assert "STRICT CONTRACT" in sent_prompt
        assert "RAW STRUCTURED INSIGHT" in sent_prompt
        assert "PREBUILT FALLBACK PROMPT" not in sent_prompt

    @pytest.mark.asyncio
    async def test_generate_success(self, coder, mock_provider, tmp_path):
        mock_provider.generate.return_value = GenerationResult(
            text="```python:solution.py\ndef sort(arr):\n    return sorted(arr)\n```",
            total_tokens=50,
        )
        result = await coder.generate(
            prompt="implement sorting",
            context={"language": "python", "task_description": "sort integers"},
            working_dir=str(tmp_path),
        )
        assert result.is_success
        assert "solution.py" in result.generated_files
        assert (tmp_path / "solution.py").exists()
        mock_provider.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_no_code_in_response(self, coder, mock_provider, tmp_path):
        mock_provider.generate.return_value = GenerationResult(
            text="I'm sorry, I can't generate code.",
            total_tokens=10,
        )
        result = await coder.generate(
            prompt="implement sorting",
            context={"language": "python"},
            working_dir=str(tmp_path),
        )
        # The response has no code blocks, but _extract_code falls back to raw text,
        # and "I'm sorry..." is not valid Python, so it should fail validation
        assert result.status in (GenerateStatus.FAILED, GenerateStatus.PARTIAL)

    @pytest.mark.asyncio
    async def test_generate_with_invalid_python(self, coder, mock_provider, tmp_path):
        mock_provider.generate.return_value = GenerationResult(
            text="```python:bad.py\ndef f(\n```",
            total_tokens=10,
        )
        result = await coder.generate(
            prompt="implement something",
            context={"language": "python"},
            working_dir=str(tmp_path),
        )
        assert result.status == GenerateStatus.FAILED

    @pytest.mark.asyncio
    async def test_generate_provider_exception(self, coder, mock_provider, tmp_path):
        mock_provider.generate.side_effect = RuntimeError("API error")
        result = await coder.generate(
            prompt="implement sorting",
            context={},
            working_dir=str(tmp_path),
        )
        assert result.status == GenerateStatus.FAILED
        assert "API error" in result.error_message

    @pytest.mark.asyncio
    async def test_generate_multi_file(self, coder, mock_provider, tmp_path):
        mock_provider.generate.return_value = GenerationResult(
            text=(
                "```python:main.py\nfrom utils import helper\nhelper()\n```\n"
                "```python:utils.py\ndef helper():\n    pass\n```"
            ),
            total_tokens=80,
        )
        result = await coder.generate(
            prompt="implement with helper",
            context={"language": "python"},
            working_dir=str(tmp_path),
        )
        assert result.is_success
        assert "main.py" in result.generated_files
        assert "utils.py" in result.generated_files


# ---------------------------------------------------------------------------
# CustomNaiveCoder.generate - mutation mode
# ---------------------------------------------------------------------------


class TestGenerateMutation:
    """Tests for mutation mode (parent_code present)."""

    @pytest.mark.asyncio
    async def test_mutation_replaces_evolve_block(self, coder, mock_provider, tmp_path):
        parent_code = {
            "sort.py": (
                "# EVOLVE_START\n"
                "def sort(arr):\n"
                "    return sorted(arr)\n"
                "# EVOLVE_END\n"
            )
        }
        mock_provider.generate.return_value = GenerationResult(
            text="```python:sort.py\ndef sort(arr):\n    arr.sort()\n    return arr\n```",
            total_tokens=40,
        )
        result = await coder.generate(
            prompt="use in-place sort",
            context={
                "parent_code": parent_code,
                "language": "python",
                "task_description": "sort",
            },
            working_dir=str(tmp_path),
        )
        assert result.is_success or result.is_partial
        assert "sort.py" in result.generated_files
        content = (tmp_path / "sort.py").read_text(encoding="utf-8")
        assert "arr.sort()" in content

    @pytest.mark.asyncio
    async def test_mutation_no_evolve_block_copies_file(self, coder, mock_provider, tmp_path):
        parent_code = {"config.py": "CONFIG = {}"}
        await coder.generate(
            prompt="improve",
            context={"parent_code": parent_code, "language": "python"},
            working_dir=str(tmp_path),
        )
        # File without EVOLVE blocks should be copied as-is
        assert (tmp_path / "config.py").exists()
        assert (tmp_path / "config.py").read_text(encoding="utf-8") == "CONFIG = {}"


# ---------------------------------------------------------------------------
# ClaudeCode - init & properties
# ---------------------------------------------------------------------------


class TestClaudeCode:
    """Tests for ClaudeCode coder initialization."""

    def test_name_property(self):
        with patch.dict("os.environ", {}, clear=False):
            from llm4ad.coder.claude_code import ClaudeCode

            config = ClaudeCodeConfig(type="claude_code")
            provider_config = ProviderConfig(
                name="test",
                type="anthropic",
                auth_token="test-token",
                base_url="https://api.test.com",
                model="claude-test",
            )
            coder = ClaudeCode(config=config, provider_config=provider_config)
            assert coder.name == "Claude Code"

    def test_env_vars_set(self):
        import os

        with patch.dict("os.environ", {}, clear=False):
            from llm4ad.coder.claude_code import ClaudeCode

            config = ClaudeCodeConfig(type="claude_code")
            provider_config = ProviderConfig(
                name="test",
                type="anthropic",
                api_key="test-token-123",
                base_url="https://api.example.com",
                model="claude-test-model",
            )
            ClaudeCode(config=config, provider_config=provider_config)
            assert os.environ["ANTHROPIC_API_KEY"] == "test-token-123"
            assert os.environ["ANTHROPIC_BASE_URL"] == "https://api.example.com"
            assert os.environ["ANTHROPIC_MODEL"] == "claude-test-model"
            assert os.environ["PYTHONUTF8"] == "1"

    def test_agent_type(self):
        with patch.dict("os.environ", {}, clear=False):
            from llm4ad.coder.claude_code import ClaudeCode

            config = ClaudeCodeConfig(type="claude_code")
            provider_config = ProviderConfig(
                name="test",
                type="anthropic",
                auth_token="t",
                base_url="https://x.com",
                model="m",
            )
            coder = ClaudeCode(config=config, provider_config=provider_config)
            assert coder.agent_type == CodingAgentType.CLAUDE_CODE
