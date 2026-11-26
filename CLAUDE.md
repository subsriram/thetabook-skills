# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this repo is

A registry of contributed slash-command skills for [thetabook](https://github.com/subsriram/thetabook) (an options-trading journal/copilot). Each skill is a markdown file with YAML frontmatter that the Claude Code harness loads as a slash command.

This repo is a **catalog**, not a runtime. The skills are consumed by `thetabook skills install <slug>` from a separate project. There is no application to run here — the only executable workflow is **lint**.

## Layout

- `core/` — curated, maintainer-reviewed, default-installed. Empty today.
- `community/` — author-vouched, opt-in. Skills start here.
- Each skill is either `<tier>/<slug>/SKILL.md` (folder form, may ship `*.py` helpers) or `<tier>/<slug>.md` (flat form).
- `.github/workflows/lint.yml` — runs `thetabook skills lint <tier> --fail-on-error` on push/PR.
- [README.md](README.md) — user-facing overview.
- [CONTRIBUTING.md](CONTRIBUTING.md) — full skill format spec and trust-contract rules. **Read this before authoring or editing a skill.**

## Trust contract (lint enforces these — don't bypass)

When editing or reviewing a skill, the following are **error-severity** and will fail CI:

- `reads` / `writes` paths must live under `personal/`. Repo-source paths (`src/`, `tests/`) are rejected.
- `writes:` must NOT include reserved paths: `personal/snapshots/` or `personal/wiki/ideas.md` (owned by `thetabook refresh` / `thetabook wiki render-ideas`).
- Declaring `writes:` requires `Edit`, `Write`, or `Bash` in `allowed-tools` — no contract/tool mismatch.
- Body must not hard-code `personal/` paths with ticker-shaped neighbors (PII leak — `personal/ideas/003-AAPL-cc.md` is rejected, generic descriptions are fine).
- Frontmatter `name` must match file/folder stem.
- `version` must be valid `X.Y` or `X.Y.Z`.

Soft warnings (don't block but worth fixing): `description` under 40 chars, body shells out without `Bash` in `allowed-tools`, `network:` declared without `Bash`/`WebFetch`.

## Conventions

- New skills go in `community/` — never open a PR directly to `core/`. Promotion is the path.
- New skills start at `version: 0.1.0`. Bump on observable behavior change (semver).
- Skill `description:` should be a paragraph (≥40 chars) — the LLM uses it for trigger matching.
- Personal/opinionated overrides do NOT belong here — those live in the consuming repo as `.local` skills (gitignored upstream).
- Helper scripts run via `uv run python "${CLAUDE_PROJECT_DIR:-$PWD}/.claude/skills/<slug>/<helper>.py"` (`thetabook skills install` copies skills into `.claude/skills/`) and must exit cleanly on `--help`. Don't hard-code `~/.local/bin/uv`: a Homebrew uv lives elsewhere. Find uv the way `community/scan-watchlist` does (its SKILL.md invocation line, and `_find_uv` in `scan.py` for the CLI calls it makes).

## Linting locally

```bash
thetabook skills lint community/<slug> --fail-on-error
```

Requires `thetabook` installed (`pip install "thetabook==2.4.0"`, the version CI pins in `.github/workflows/lint.yml`). The CI workflow runs the same lint against `core/` and `community/` separately.

## Don't

- Don't write to `core/` directly when adding a new skill.
- Don't edit `README.skills.md` — it's CI-generated and gitignored.
- Don't add license headers or pick a license — `README.md` flags this as a TODO for the maintainer.
- Don't introduce skills that read/write outside `personal/`, even via Bash — the trust contract is the whole point of the registry.
