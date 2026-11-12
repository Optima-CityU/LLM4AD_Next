"""Create editable gateway workspaces from resources shipped in the SDK wheel."""

import json
from importlib.resources import files
from pathlib import Path

from llm4ad import __version__
from llm4ad.gateway.configuration import user_ids, validate_server

RESOURCES = {
    "templates/custom/Dockerfile": "Dockerfile",
    "templates/custom/compose.yaml": "compose.yaml",
    "templates/custom/compose.ui.yaml": "compose.ui.yaml",
    "templates/custom/env.example": ".env.example",
    "examples/basic/evaluator.py": "evaluator.py",
    "examples/basic/runner.py": "runner.py",
    "examples/basic/candidate/solution.py": "candidate/solution.py",
}


def initialize(
    directory: Path, *, server: str | None = None, name: str = "local-evaluator", allowed_users: list[str] | None = None
) -> list[Path]:
    """Write a complete workspace without overwriting any existing destination.

    Args:
        directory: Empty or partially populated user-owned destination.
        server: Optional platform base URL for the generated environment.
        name: Human-readable gateway name.
        allowed_users: Explicit platform user UUIDs.

    Returns:
        Paths of the generated workspace files.
    """
    if not name.strip() or "\n" in name or "\r" in name:
        raise ValueError("name must be a nonempty single line")
    server = validate_server(server) if server else "https://llm4ad-next.cn"
    allowed = user_ids(allowed_users or [])
    substitutions = {
        "SDK_VERSION": __version__,
        "SERVER": json.dumps(server),
        "NAME": json.dumps(name, ensure_ascii=False),
        "USERS": json.dumps(",".join(allowed)),
    }
    resources = files("llm4ad.gateway")
    contents = {}
    sources = dict(RESOURCES)
    if resources.joinpath("templates/custom/README.md").is_file():
        sources["templates/custom/README.md"] = "README.md"
    for source, target in sources.items():
        content = resources.joinpath(source).read_text(encoding="utf-8")
        for key, value in substitutions.items():
            content = content.replace("{{" + key + "}}", value)
        contents[target] = content
    contents[".env"] = contents[".env.example"]
    contents["requirements.txt"] = "# Add dependencies required by your evaluator.\n"
    contents[".gitignore"] = ".env\nstate/\n.llm4ad-gateway*/\nwheels/*.whl\n__pycache__/\n"
    contents["wheels/.gitkeep"] = ""
    root = directory.expanduser().absolute()
    for target in contents:
        path = root / target
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"Refusing to overwrite {path}")
        for parent in path.parents:
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise ValueError(f"Destination parent must be a regular directory: {parent}")
    for target, content in contents.items():
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    return [root / target for target in contents]
