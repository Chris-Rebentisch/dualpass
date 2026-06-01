---
name: research-reviewer
description: Judge the research-author's artifact via the compute-floor + live-fire-probe contract.
context_sources:
  - units/{unit_id}/research-v{round}.md
artifacts_produced:
  - units/{unit_id}/research-v{round}-review.md
success_criteria:
  - Verdict line emitted: "**Verdict:** APPROVED" or "**Verdict:** REJECTED"
  - At least 5 named verification categories covered
  - Wall-clock ≥ 60s OR output ≥ 10k tokens (compute floor)
---

# research-reviewer skill (template)

Reviewers in dualpass MUST run live-fire probes (file existence checks, grep, schema lookups) rather than trusting the author's claims. Self-review by AI is reliably too lenient; cross-vendor adversarial review by a different model catches what same-vendor review misses.

## Compute floor (mandatory)

- Cover at least 5 named verification categories (structure, linkage, fidelity, correctness, completeness).
- Run for ≥ 60 seconds wall-clock OR emit ≥ 10,000 output tokens, whichever comes first.
- Brief reviews that skip categories MUST return REJECTED with the gap named.

## Live-fire probes (mandatory)

Pick the probes appropriate to this stage. Examples:

- File existence: `test -f <path>`
- Symbol existence: `grep -n "symbol_name" path/file.ext`
- Test collection: `pytest --collect-only -q`
- Lint: `ruff check`, language-specific equivalents
- Build: project's build command

If a probe contradicts the author's claim, that is a blocker finding.

## Output

Write `units/{unit_id}/research-v{round}-review.md` with:

- The verdict line (see `success_criteria`)
- One section per finding, severity-tagged (mechanical | design)
- The probe results table
