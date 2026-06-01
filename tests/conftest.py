"""Test-wide helpers shared by fixtures.

The bundled `examples/coding-agent/config/stages.yaml` ships with
`research.reviewer_skill: null` (v1.0.2 — research is exploration, not
judgment; default config skips the LLM review on that stage). Most
controller tests were written before that change and assume every
stage produces a review file. This helper re-enables the research
reviewer in a scaffolded project so those tests stay reflective of
the harness mechanics, not the default configuration choice.
"""

from __future__ import annotations

from pathlib import Path


def enable_research_reviewer(project_root: Path) -> None:
    """Flip `research.reviewer_skill: null` back to a real skill path.

    Idempotent. Safe to call after `_init.run_init(target)` whether or
    not the bundled config currently nulls the research reviewer.
    """
    stages_path = project_root / "config" / "stages.yaml"
    text = stages_path.read_text(encoding="utf-8")
    if "reviewer_skill: null" in text:
        text = text.replace(
            "reviewer_skill: null\n    preflight_gates:\n      - check-frontmatter\n    max_rounds: 6\n    breakpoint_default: false",
            "reviewer_skill: skills/research/REVIEWER.md\n    preflight_gates:\n      - check-frontmatter\n    max_rounds: 6\n    breakpoint_default: false",
            1,
        )
        stages_path.write_text(text, encoding="utf-8")
