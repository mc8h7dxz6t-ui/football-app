# hibs-bet evidence-engine patch — transport / hand-off

`hibs-bet-evidence-engine.patch` holds **23 commits** that were built against the
`mc8h7dxz6t-ui/hibs-bet` repo but could not be pushed from the `football-app`-scoped
cloud agent. This branch (in `football-app`) is **transport only** — do not merge it
here. Apply the patch in `hibs-bet`.

## What the patch contains
- **TRD-089** tick-to-trade telemetry wired into the live trading hot path.
- **Significance evidence engine** (`evidence_stats.py`): bootstrap ROI CIs, Wilson
  proportion CIs, significance-aware grading; blended frequency×magnitude gates
  (CLV + value legs); wired into `gate_profile_compare` and `/api/health`.
- **Threshold sweep harness** (`evidence_sweep.py`) — liquidity-weighted F-β.
- Pre-existing CI failures fixed (players template, flaky gate test, prediction_log_auto).
- Docs: `PROP_DESK_STRATEGY.md`, `TRADING_REPO_EXTRACTION_PLAN.md`,
  `INST_PLUS_PLUS_ROADMAP.md`, `RACING_GATE_COVERAGE_INST_PLUS.md` +
  `handoff/racing_gate_reference.py` (tested).

## How to apply (in the hibs-bet repo, with write access)
```bash
git clone https://github.com/mc8h7dxz6t-ui/hibs-bet && cd hibs-bet
git checkout -b cursor/trd-089-latency-telemetry-8e88

# grab the patch from the football-app transport branch:
curl -L -o evidence.patch \
  https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/cursor/hibs-bet-transport-8e88/hibs-bet-evidence-engine.patch

git am evidence.patch          # applies all 23 commits
git rebase origin/main         # branch was cut at fa22a8e; reconcile if main advanced
python -m pytest tests/ -q     # expect green (622 + handoff 8)
git push -u origin cursor/trd-089-latency-telemetry-8e88
```

If `git am` hits a conflict (only likely in `strategy_runner.py` if `origin/main`
advanced), resolve, `git am --continue`. Then open a PR into `hibs-bet` `main`.
