"""Built-in gate plugins. Each gate is a callable returning (passed: bool, detail: str).

Built-ins shipping in v0.2.0:
  - check_frontmatter
  - check_line_citations  (with substring containment for tokens ≤ SYMBOL_LEN_CEILING)
  - check_single_flight
  - check_marker_frontmatter
  - check_ac1_wording

Projects can add their own under <project_root>/gates/<stage>/<name>.{sh,py}.

v0.1.0a0 status: stub package. Built-ins land per the v0.2.0 milestone in CHANGELOG.md.
"""

from __future__ import annotations

__all__: list[str] = []
