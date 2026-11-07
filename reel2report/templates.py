"""Prompt templates: built-in scene presets plus user-supplied files and instructions.

A template is a Markdown file with optional front matter and three sections:

    ---
    name: my-scene
    description: one line shown by --list-templates
    ---
    ## role      who the model is
    ## step      what to pay attention to on each screenshot
    ## summary   how to write the final report

The JSON output format is NOT part of the template: it is fixed in code so the
result can always be parsed. Templates only change focus, tone and judgement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

BUILTIN_DIR = Path(__file__).parent / "templates"
DEFAULT = "bug"


@dataclass
class Template:
    name: str
    description: str
    role: str
    step: str
    summary: str


def parse(text: str, fallback_name: str = "custom") -> Template:
    meta: dict[str, str] = {}
    fm = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if fm:
        for line in fm.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        text = text[fm.end():]
    sections: dict[str, str] = {}
    for m in re.finditer(r"^##\s*(\w+)\s*\n(.*?)(?=^##\s*\w+\s*\n|\Z)", text, re.S | re.M):
        sections[m.group(1).lower()] = m.group(2).strip()
    if not sections:  # plain text file: treat the whole thing as step guidance
        sections["step"] = text.strip()
    return Template(
        name=meta.get("name", fallback_name),
        description=meta.get("description", ""),
        role=sections.get("role", ""),
        step=sections.get("step", ""),
        summary=sections.get("summary", ""),
    )


def builtin_names() -> list[str]:
    return sorted(p.stem for p in BUILTIN_DIR.glob("*.md"))


def load(name_or_path: str | None) -> Template:
    """A built-in name (bug / ux / tutorial) or a path to a template file."""
    name_or_path = name_or_path or DEFAULT
    builtin = BUILTIN_DIR / f"{name_or_path}.md"
    if builtin.exists():
        return parse(builtin.read_text("utf-8"), name_or_path)
    path = Path(name_or_path)
    if path.exists():
        base = load(DEFAULT)
        t = parse(path.read_text("utf-8"), path.stem)
        # sections missing from a custom template fall back to the default preset
        return Template(t.name, t.description, t.role or base.role, t.step or base.step,
                        t.summary or base.summary)
    raise ValueError(f"未知模板：{name_or_path}（内置：{', '.join(builtin_names())}，或传入模板文件路径）")


def list_templates() -> list[Template]:
    return [load(n) for n in builtin_names()]
