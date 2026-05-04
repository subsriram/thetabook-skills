# thetabook-skills

A registry of contributed skills for [thetabook](https://github.com/subsriram/thetabook) — the journal-and-copilot for options traders.

A *skill* is a markdown file with YAML frontmatter that the Claude Code harness loads as a slash command. Skills can shell out to the `thetabook` CLI, render analysis tables, or chain together review workflows. The frontmatter is the trust contract — `allowed-tools`, `reads`, `writes`, `network` enumerate exactly what the skill is permitted to touch.

## Tiers

This registry has two tiers:

| Tier | Folder | Curated? | Default install? |
| --- | --- | --- | --- |
| **core** | [`core/`](core/) | yes — reviewed by maintainers | yes |
| **community** | [`community/`](community/) | lower bar — author-vouched | no — opt-in via `--from community` (when the registry-aware install lands) |

Personal opinionated overrides should NOT live here — those stay in your local `.claude/commands/<name>.local.md` (or `<name>.local/SKILL.md` folder form), gitignored on the consuming repo.

## Install (today)

The registry-aware installer (`thetabook skills install <slug>` against a configured registry URL) is not built yet. For now, install from a local clone:

```bash
git clone https://github.com/<user>/thetabook-skills.git ~/.cache/thetabook-skills
cd <your-thetabook-project>
thetabook skills install scan-watchlist --from ~/.cache/thetabook-skills/community
thetabook skills installed
```

The installer re-runs `thetabook skills lint --fail-on-error` before copying anything; a skill that bypassed CI by being side-loaded from a fork or mid-flight edits can't reach `.claude/commands/` without re-passing the same bar.

When `writes` or `network` are non-empty, the installer prints the declared trust contract and asks for confirmation. Pass `--yes` for scripted use; non-interactive sessions without `--yes` refuse rather than auto-approving.

## Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full flow. The short version:

1. Author your skill under `community/<slug>/SKILL.md` (folder form, optionally with `*.py` helpers) or `community/<slug>.md` (flat form).
2. Run `thetabook skills lint community/<slug>` locally — fix any error-severity issues.
3. Open a PR. CI runs `thetabook skills lint . --fail-on-error` and blocks merges on errors.
4. Promotion to `core/` happens after 30 days, ≥3 thumbs-up, and a maintainer sponsor.

## What skills can't do

- Write to `personal/snapshots` or `personal/wiki/ideas.md` — those are owned by `thetabook refresh` and `thetabook wiki render-ideas` and would be clobbered. The `skill-writes-reserved-path` lint check enforces this.
- Read or write outside `personal/` — declaring access to repo source (`src/`, `tests/`) is rejected. The `skill-path-outside-personal` lint check enforces this.
- Hard-code paths like `personal/ideas/003-AAPL-cc.md` in the body — skills are generic, the user's data isn't. The `skill-personal-pii-in-body` lint check enforces this.
- Bypass the trust contract — declaring `writes: [personal/...]` without `Edit`/`Write`/`Bash` in `allowed-tools` is rejected as internally inconsistent.

## Reserved paths

Today's reserved-write list (see `thetabook.skills.policy.RESERVED_WRITE_PATHS`):

- `personal/snapshots/` — wholesale-overwritten by `thetabook refresh`
- `personal/wiki/ideas.md` — auto-rendered by `thetabook wiki render-ideas`

Other paths under `personal/` (journal, learnings, wiki overview/underlyings/strategies, ideas/, iv_history, watchlist.toml, accounts.toml, ticker_aliases.toml) are user/skill-owned and **not** reserved.

## License

TODO — pick a license before tagging the first release.
