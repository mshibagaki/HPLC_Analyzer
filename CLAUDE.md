# CLAUDE.md

This repository keeps one shared agent contract. Claude Code, Codex, and any other
assistant follow the same documents in the same order.

1. **`AGENTS.md`** — repository rules (project purpose, Windows 7 / Windows 11
   compatibility, project-file schema, scientific behavior, Git workflow).
   This is the top-level contract and nothing below overrides it.
2. **`REQUIREMENTS_STATUS.md`** — the single ledger of implemented and remaining
   product requirements. Update it in the same PR that changes a status.
3. **`VALIDATION_BLOCKERS.md`** — external and physical validation gates.
   Never treat a CI pass as evidence for a gate recorded here.
4. **`WIN11_FEEDBACK_WORKFLOW.md`** — the current work plan: the batch definitions
   (ア行 / カ行), their order and dependencies, per-batch acceptance criteria, the
   verification commands, and the technical findings collected before implementation.
5. **`CODEX_BRIEF.md`** — the work order for the current batches: what is
   startable now, the per-issue procedure, and the constraints worth repeating.
   It is a hand-off brief, not a rule file, and applies to any assistant despite
   its name.
6. **`ACKNOWLEDGEMENTS.md`** — people whose original design or method became a
   feature. Distinct from `THIRD_PARTY_NOTICES.txt`, which covers software
   licenses.
7. Open GitHub Issues / PRs and the latest `origin/main` — check before starting
   anything, to avoid duplicating work that is already open or merged.

Do not add assistant-specific instructions to this file. If guidance applies to
the repository, it belongs in `AGENTS.md`; if it applies to the current work plan,
it belongs in `WIN11_FEEDBACK_WORKFLOW.md`.
