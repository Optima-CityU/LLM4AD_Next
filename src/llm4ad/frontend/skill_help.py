"""Read the built-in skill indexes and individual topics."""

import tomllib
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TypedDict, cast

_SKILLS = {
    "cloud-package": "llm4ad-cloud-package",
    "gateway": "llm4ad-evaluator-gateway",
}


class _Topic(TypedDict):
    description: str
    path: str


class _Skill(TypedDict):
    description: str
    steps: list[str]
    topics: dict[str, _Topic]


def _skill_directory(directory: str) -> Traversable:
    """Locate a skill in an installed wheel or editable checkout."""
    packaged = files("llm4ad").joinpath("skill_resources", directory)
    if packaged.joinpath("cli.toml").is_file():
        return packaged
    return Path(__file__).resolve().parents[3] / "skills" / directory


def _read_index(root: Traversable) -> _Skill:
    """Load the topic index maintained alongside the skill."""
    return cast(_Skill, tomllib.loads(root.joinpath("cli.toml").read_text(encoding="utf-8")))


def read_skill_help(name: str, topic: str | None = None) -> str:
    """Return a skill listing, one skill index, or one topic.

    Args:
        name: Short skill name, full skill name, or `list`.
        topic: Optional topic identifier from the skill index.

    Returns:
        Markdown text with only the requested guidance.

    Raises:
        ValueError: The skill name or topic is not available.
        OSError: An installed skill resource cannot be read.
    """
    if name == "list":
        if topic is not None:
            raise ValueError("--topic requires a named skill from `llm4ad --skill list`")
        entries = [
            f"- `{short_name}` — {_read_index(_skill_directory(directory))['description']}"
            for short_name, directory in _SKILLS.items()
        ]
        return "\n".join(["# LLM4AD skills", "", *entries, "", "Read an index with `llm4ad --skill <name>`."])

    short_name = next((key for key, directory in _SKILLS.items() if name in (key, directory)), None)
    if short_name is None:
        raise ValueError(f"Unknown skill {name!r}. Run `llm4ad --skill list`.")

    directory = _SKILLS[short_name]
    root = _skill_directory(directory)
    index = _read_index(root)
    if topic is not None:
        if topic not in index["topics"]:
            raise ValueError(f"Unknown topic {topic!r}. Run `llm4ad --skill {short_name}`.")
        path = index["topics"][topic]["path"]
        text = root.joinpath(*path.split("/")).read_text(encoding="utf-8").strip()
        return f"{text}\n\nSkill directory: `{root}`"

    lines = [
        f"# {short_name}",
        "",
        index["description"],
        "",
        *(f"{number}. {step}" for number, step in enumerate(index["steps"], 1)),
        "",
        "## Topics",
        "",
        *(
            f"- `llm4ad --skill {short_name} --topic {key}` — {value['description']}"
            for key, value in index["topics"].items()
        ),
    ]
    return "\n".join(lines)
