# hibs-bet forward evidence checklist (C1–C4) — transport / hand-off

`hibs-bet-forward-evidence-checklist.patch` adds a labelled operator checklist for the
forward B2B evidence path. Apply in **`hibs-bet`** (not `football-app`).

## Checklist

| Step | Action |
|------|--------|
| **C1** | `sudo bash deploy/cron-hibs-calibration.sh --install` on VPS |
| **C2** | Load dashboard while logged in on fixture days (seeds snapshots) |
| **C3** | `bash scripts/run_forward_backfill_plan.sh` |
| **C4** | Wait for ≥3 matchdays + `pred-log-sync` → `./scripts/verify_football_evidence_gates.sh` |

After apply, run on VPS:

```bash
./scripts/run_football_evidence_checklist.sh --watch
```

## How to apply (hibs-bet repo, write access)

**You must be inside a `hibs-bet` git clone** — `cd hibs-bet` alone fails if you never cloned.

```bash
# 1) Clone (skip if you already have the repo — then cd into it)
git clone https://github.com/mc8h7dxz6t-ui/hibs-bet.git ~/hibs-bet
cd ~/hibs-bet

# 2) Branch + patch (https:// required on the curl URL)
git checkout -b cursor/forward-evidence-checklist-c4a1

curl -L -o checklist.patch \
  https://raw.githubusercontent.com/mc8h7dxz6t-ui/football-app/cursor/forward-evidence-checklist-c4a1/hibs-bet-forward-evidence-checklist.patch

git am checklist.patch
python3 -m pytest tests/test_forward_evidence.py -q
git push -u origin cursor/forward-evidence-checklist-c4a1
```

If you already downloaded `checklist.patch` in `~`, you can reuse it:

```bash
cd ~/hibs-bet   # your existing clone
git checkout -b cursor/forward-evidence-checklist-c4a1
git am ~/checklist.patch
```

Open a PR into `hibs-bet` `main`, then on VPS:

```bash
cd /opt/hibs-bet && git pull
sudo bash deploy/cron-hibs-calibration.sh --install          # C1
# C2: dashboard on fixture days (or --seed)
bash scripts/run_forward_backfill_plan.sh                    # C3
./scripts/run_football_evidence_checklist.sh --watch       # C4
```
