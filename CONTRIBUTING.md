# Contributing a skill

Thanks for considering a contribution. This doc covers the format, the contribution flow, and the trust contract that keeps installed skills safe.

## Tiers

| Tier | Folder | Bar | Promotion |
| --- | --- | --- | --- |
| **core** | `core/` | curated, reviewed by maintainers, default-install | from community after 30 days, ≥3 thumbs-up reactions, and a maintainer sponsor |
| **community** | `community/` | author-vouched, must pass lint, no other gate | start here |

Start in `community/`. Don't open a PR directly to `core/` — promotion is the natural path.

## Skill format

Every skill is a markdown file with YAML frontmatter, in one of two on-disk forms:

```
community/<slug>/SKILL.md      # folder form — preferred when the skill ships a Python helper
community/<slug>/scan.py       # ↑ helpers go alongside SKILL.md
community/<slug>.md            # flat form — fine for skills that are pure prose
```

The folder form is preferred when there's any code beyond the SKILL.md body — Python files get syntax-checked separately, and the trust contract stays cleanly at the markdown frontmatter.

### Required frontmatter

```yaml
---
name: <slug>                          # must match folder/file name; lowercase letters, digits, hyphens
description: |                        # one paragraph, ≥40 chars; the LLM uses this for trigger matching
  What the skill does, when to invoke it, what it expects.
version: 0.1.0                        # semver; bump on behavior change
tier: community                       # community | core
allowed-tools: [Bash, Read]           # subset of: Bash, Read, Edit, Write, Glob, Grep, WebFetch, WebSearch, TodoWrite, Task
reads:  [personal/snapshots]          # personal/ paths the skill reads (empty list = none)
writes: []                            # personal/ paths the skill writes (empty list = read-only)
network: [yfinance]                   # external services: schwab | massive | yfinance | http
---
```

### Optional frontmatter

```yaml
author: github-handle                 # for review pings
requires:
  - thetabook>=1.8.2                  # CLI version constraint
tags: [analysis, ideas]
when-to-use: |                        # finer trigger guidance
  User asks "find me trades", "what should I sell?", references a watchlist.
examples:
  - "scan my watchlist for short puts under 30 delta"
```

## Trust contract — what skills MUST NOT do

The lint enforces the following at PR time and again at install time:

- **`skill-path-outside-personal`** (error). Every entry in `reads` and `writes` must live under `personal/`. Declaring access to repo source (`src/...`, `tests/...`) is rejected.
- **`skill-writes-reserved-path`** (error). The following paths are owned by thetabook and must NOT appear in `writes`:
  - `personal/snapshots/` — overwritten by `thetabook refresh`
  - `personal/wiki/ideas.md` — auto-rendered by `thetabook wiki render-ideas`

  See `thetabook.skills.policy.RESERVED_WRITE_PATHS` for the canonical list.
- **`skill-writes-without-tools`** (error). Declaring `writes:` without `Edit`/`Write`/`Bash` in `allowed-tools` is rejected — the contract is internally inconsistent.
- **`skill-personal-pii-in-body`** (error). The body must not hard-code paths like `personal/ideas/003-AAPL-cc.md` (a `personal/` path with a ticker-shaped neighbor on the same line). Describe paths generically — the user's data is theirs.
- **`skill-name-mismatch`** (error). Frontmatter `name` must match the file/folder stem.
- **`skill-version-bad`** (error). `version` must be valid `X.Y` or `X.Y.Z`.

Soft signals (warn, not block):

- **`skill-description-too-short`**: descriptions under 40 chars degrade the LLM's trigger-matching.
- **`skill-no-allowed-tools`**: the body looks like it shells out (` ```bash `, `\`thetabook ...\``) but `allowed-tools:` is empty.
- **`skill-network-without-bash`**: `network:` declared but no `Bash`/`WebFetch` in tools — the call has nowhere to go.

## PR checklist

- [ ] Folder/file name matches frontmatter `name`.
- [ ] `version: 0.1.0` for new skills.
- [ ] `description` is a paragraph, not a sentence.
- [ ] `reads` / `writes` exhaustively list `personal/` paths; CI checks the body matches the declared contract.
- [ ] No `personal/` literals with ticker-shaped neighbors in the body.
- [ ] Ran `thetabook skills lint community/<slug>` locally — clean.
- [ ] If shipping a `*.py` helper: it runs as `uv run python <path-to-helper>`, finding uv wherever it is installed (see `community/scan-watchlist`), and exits cleanly on `--help`.

## CI

`.github/workflows/lint.yml` runs `thetabook skills lint . --fail-on-error` on every push and PR. PRs that introduce error-severity issues fail the check; warnings don't block.

## Versioning + behavior changes

Bump `version` whenever the skill's observable behavior changes (output format, flag semantics, side effects). The patch level (X.Y.**Z**) is for bug fixes; minor (X.**Y**.Z) for new features; major (**X**.Y.Z) for breaking changes.

When you bump major, update any `requires:` constraints downstream skills declare. Today no skill requires another skill, but the schema supports it.
