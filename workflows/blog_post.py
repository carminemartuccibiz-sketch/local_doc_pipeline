"""Workflow generazione blog post (stub blueprint)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from workflows.base_workflow import BaseWorkflow


class BlogPostWorkflow(BaseWorkflow):
    def process_file(self, file_path: Path, ctx: dict[str, Any]) -> Any:
        raise NotImplementedError("blog_post workflow — da implementare")
