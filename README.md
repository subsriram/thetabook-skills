# thetabook-skills

A registry of contributed slash-command skills for [thetabook](https://github.com/subsriram/thetabook) — the journal-and-copilot for options traders.

A *skill* is a markdown file with YAML frontmatter that the Claude Code harness loads as a slash command. Skills can shell out to the `thetabook` CLI, render analysis tables, or chain together review workflows. The frontmatter is the **trust contract** — `allowed-tools`, `reads`, `writes`, `network` enumerate exactly what the skill is permitted to touch.

> **New to thetabook?** Skills are useless without the CLI. Install and read about it first: [github.com/subsriram/thetabook](https://github.com/subsriram/thetabook). Full command reference: [docs/cli.md](https://github.com/subsriram/thetabook/blob/main/docs/cli.md). `thetabook <subcommand> --help` works too and reflects your installed version.

## Tiers

| Tier | Folder | Curated? | Default install? |
| --- | --- | --- | --- |
| **core** | [`core/`](core/) | yes — reviewed by maintainers | yes |
| **community** | [`community/`](community/) | lower bar — author-vouched, must pass lint | no — opt-in via `--from community` (when the registry-aware install lands) |

Personal opinionated overrides do NOT live here — keep those in your local `.claude/commands/<name>.local.md` (or `<name>.local/SKILL.md` folder form), gitignored on the consuming repo.

---

## Using this repo (install a skill)

The registry-aware installer (`thetabook skills install <slug>` against a configured registry URL) is not built yet. For now, install from a local clone.

### 1. Clone the registry

```bash
git clone https://github.com/subsriram/thetabook-skills.git ~/.cache/thetabook-skills
```

### 2. Install a skill into your thetabook project

From the root of *your* thetabook project (the one with `personal/`):

```bash
thetabook skills install scan-watchlist --from ~/.cache/thetabook-skills/community
```

The installer:

1. Re-runs `thetabook skills lint --fail-on-error` against the source skill — a skill that bypassed CI by being side-loaded from a fork or mid-flight edit can't reach `.claude/commands/` without re-passing the same bar.
2. Prints the declared trust contract (`reads`, `writes`, `network`) when `writes` or `network` are non-empty, and asks for confirmation. Pass `--yes` for scripted use; non-interactive sessions without `--yes` refuse rather than auto-approving.
3. Copies the skill (and any `*.py` helpers) into `.claude/commands/<slug>/`.

### 3. Verify

```bash
thetabook skills installed
```

### 4. Use it

Inside Claude Code, invoke the slash command — `/scan-watchlist`, etc.

### Updating

`thetabook skills install` is idempotent — re-run the same command after a `git pull` in `~/.cache/thetabook-skills` to pick up new versions. The installer compares frontmatter `version` and prompts on downgrade.

### Uninstalling

Delete the skill folder/file from `.claude/commands/` in your project. There's no separate uninstall command yet.

---

## Contributing a skill

The full spec lives in [CONTRIBUTING.md](CONTRIBUTING.md). Quick path:

### 1. Fork & branch

```bash
git clone https://github.com/<you>/thetabook-skills.git
cd thetabook-skills
git checkout -b add-<slug>
```

### 2. Author your skill

Place it under `community/` — never open a PR directly to `core/`. Promotion to `core/` happens after 30 days, ≥3 thumbs-up reactions, and a maintainer sponsor.

Two on-disk forms:

```
community/<slug>/SKILL.md      # folder form — preferred when the skill ships a Python helper
community/<slug>/<helper>.py   # ↑ helpers go alongside SKILL.md
community/<slug>.md            # flat form — fine for skills that are pure prose
```

Minimum viable frontmatter:

```yaml
---
name: <slug>                          # must match folder/file stem
description: |                        # one paragraph, ≥40 chars; the LLM uses this for trigger matching
  What the skill does, when to invoke it, what it expects.
version: 0.1.0                        # semver; bump on behavior change
tier: community                       # always start here
allowed-tools: [Bash, Read]           # subset of: Bash, Read, Edit, Write, Glob, Grep, WebFetch, WebSearch, TodoWrite, Task
reads:  [personal/snapshots]          # personal/ paths the skill reads
writes: []                            # personal/ paths the skill writes
network: [yfinance]                   # external services: schwab | massive | yfinance | http
---
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full schema (optional fields like `author`, `requires`, `tags`, `when-to-use`, `examples`).

### 3. Lint locally

```bash
pip install "thetabook==2.4.0"
thetabook skills lint community/<slug> --fail-on-error
```

Fix any **error**-severity findings before opening a PR. Warnings don't block but are usually worth addressing.

### 4. Open a PR

CI ([`.github/workflows/lint.yml`](.github/workflows/lint.yml)) runs `thetabook skills lint . --fail-on-error` on every push and PR; errors block merge.

### PR checklist

- [ ] Folder/file name matches frontmatter `name`.
- [ ] `version: 0.1.0` for new skills.
- [ ] `description` is a paragraph, not a sentence.
- [ ] `reads` / `writes` exhaustively list `personal/` paths the body actually touches.
- [ ] No `personal/` literals with ticker-shaped neighbors in the body.
- [ ] Local lint clean.
- [ ] Any `*.py` helper runs as `~/.local/bin/uv run python <path>` and exits cleanly on `--help`.

### CLI reference for skill authors

Skills typically shell out to `thetabook` subcommands (`price`, `chain`, `earnings`, `refresh`, etc.). Reference: [thetabook CLI docs](https://github.com/subsriram/thetabook/blob/main/docs/cli.md). Run `thetabook <subcommand> --help` for the flags/JSON shapes that match your installed version. Pin a `requires:` constraint in your frontmatter (e.g. `thetabook>=1.8.2`) when relying on a specific CLI version.

### Existing skills

Browse [`community/`](community/) for examples. [`community/scan-watchlist`](community/scan-watchlist) is the reference implementation — folder form, ships a Python helper, declares `reads` + `network`, no `writes`.

---

## What skills can't do

The lint enforces these at PR time and again at install time:

- **Write to `personal/snapshots/` or `personal/wiki/ideas.md`** — those are owned by `thetabook refresh` and `thetabook wiki render-ideas` and would be clobbered. (`skill-writes-reserved-path`)
- **Read or write outside `personal/`** — declaring access to repo source (`src/`, `tests/`) is rejected. (`skill-path-outside-personal`)
- **Hard-code paths like `personal/ideas/003-AAPL-cc.md` in the body** — skills are generic, the user's data isn't. (`skill-personal-pii-in-body`)
- **Bypass the trust contract** — declaring `writes: [personal/...]` without `Edit`/`Write`/`Bash` in `allowed-tools` is rejected as internally inconsistent. (`skill-writes-without-tools`)

## Reserved paths

Today's reserved-write list (canonical source: `thetabook.skills.policy.RESERVED_WRITE_PATHS`):

- `personal/snapshots/` — wholesale-overwritten by `thetabook refresh`
- `personal/wiki/ideas.md` — auto-rendered by `thetabook wiki render-ideas`

Other paths under `personal/` (journal, learnings, wiki overview/underlyings/strategies, ideas/, iv_history, watchlist.toml, accounts.toml, ticker_aliases.toml) are user/skill-owned and **not** reserved.

## Versioning

Bump `version` whenever observable behavior changes (output format, flag semantics, side effects). Patch (X.Y.**Z**) for bug fixes, minor (X.**Y**.Z) for new features, major (**X**.Y.Z) for breaking changes.

## License

TODO — pick a license before tagging the first release.
