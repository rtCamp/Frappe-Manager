---
name: no-noise-comments
description: "Comments must state a WHY/invariant the code cannot — no banners, step narration, or restate-the-code comments"
condition: ["#\\s*save config", "#\\s*={3,}\\s*helpers\\s*={3,}", "#\\s*Step \\d+", "#\\s*remove the self parameter"]
scope: "text"
---

This output contains noise-comment patterns that violate the repo Comment Convention (AGENTS.md, STRICT).

A comment earns its lines only if it says something the code cannot. Before emitting one, delete it mentally — if a competent reader loses nothing, do not write it.

- FORBIDDEN: section banners (`# === helpers ===`, `# ---- access`), `# Step N:` narration, comments restating the adjacent call (`# save config` over `config.save()`), commented-out code, stale references to removed flags/params.
- KEEP only WHY/invariant/trap comments (external facts, footguns, deliberate weirdness) and one-line `# noqa` rationales.
- FORM: one sharp line preferred; no discovery stories, no `we used to…` history.
- UNTOUCHABLE: typer docstrings, `help=`, `@example` text (rendered CLI help) and template comments (generated output).

When writing or reviewing code, drop the noise comment or replace it with the single load-bearing invariant it was hiding.