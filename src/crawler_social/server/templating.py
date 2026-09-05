"""Jinja2 environment for the viewer, with autoescaping forced on.

Post text, author names, and page URLs are attacker-controlled third-party
content. Every template is autoescaped and no template may use `| safe` on
a value that came out of the database. The loader path is derived from this
file's location so the server runs from any working directory.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .format import duration, excerpt, format_bytes, highlight, relative_age

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def create_templates() -> Jinja2Templates:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(enabled_extensions=("html", "xml")),
    )
    env.filters["bytes"] = format_bytes
    env.filters["excerpt"] = excerpt
    env.filters["highlight"] = highlight
    env.filters["ago"] = relative_age
    env.filters["duration"] = duration
    return Jinja2Templates(env=env)
