---
name: agents-md-living-doc
description: "When touching AGENTS.md, keep it a complete living doc — surfaces, decisions, learnings — follow its conventions, and guard what not to write"
condition: ["load-bearing source of truth", "Reconcile all three kinds of knowledge"]
scope: "text"
---

You are treating AGENTS.md as this repo's living source of truth. Do not stop at describing it or landing a one-line patch — make the change complete, durable, and convention-true.

**Reconcile all three kinds of knowledge in the same change:**
1. **Surfaces** — added/renamed/removed a Typer command, a `just` recipe, or a `[project.scripts]` entry: update the matching section (Developer Commands, Architecture) so the mirror stays accurate.
2. **Decisions** — record non-obvious choices a future agent could undo, WITH the rationale (why `uv` not `poetry`, why fmx is excluded from `just test`, why there is no `fail_under` in pyproject). A decision without its "why" gets reverted.
3. **Learnings** — add a Gotchas bullet for any footgun you hit and resolved (rich eating `\[table]` markup, help text never hard-wrapping) so nobody rediscovers it.

**Follow existing conventions — match, never re-style:**
- Add to the section and heading level that already exists; do not invent a parallel one.
- Match voice and formatting: fenced `bash` blocks for commands, `just <recipe>` names verbatim, tables for routing/workflow matrices, Gotchas bullets that lead with the footgun.
- Escape rich-style markup exactly as the file already does (`\[table]`, `\[build]`) — an unescaped `[word]` renders as nothing.
- Preserve quote style and line-length norms; do not reflow or normalize existing text.

**Guard what NOT to write:**
- This file is explicitly NEVER-commit / gitignored — never add anything that assumes it ships to other users or CI.
- No secrets, tokens, absolute personal paths, or machine-specific state.
- No ephemeral session context, one-off task narration, or transient TODOs — only durable, repo-true knowledge.
- Do not duplicate a fact already covered; extend or correct the existing bullet instead.
- Never delete a decision's rationale.

Do not report the task done until the file reflects the surface change, the decision, and the learning in the correct sections and conventions — or you have explicitly confirmed none applies.