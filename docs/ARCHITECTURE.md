# Architecture — Value Engine vs Institutional++

This document maps the **Football Value Engine (FVE)** stack to the “Institutional++”
low-latency blueprint, with honest constraints and a phased upgrade path.

## What actually limits you today

| Constraint | Typical scale | Institutional++ fix relevance |
|------------|---------------|------------------------------|
| Book API rate limits (Matchbook, Odds API shared with other repos) | 15–1200 calls/hr | **Critical** — `pipeline/rate_limits.py` |
| REST poll interval (0.5–5s tiered) | Hundreds of ms | Dominates latency, not JSON |
| Exchange price freshness | 1–3s blind spots | Peak ZSET window + faster Matchbook poll |
| UI reactivity | 100ms–2s acceptable | WebSocket hub removes REST polling |
| JSON parse in Python | µs per message | **Not** the bottleneck until >>1k ticks/s |

Kernel bypass (DPDK), Aeron IPC, and Rust gateways solve problems you do not have on a
research / value-betting stack polling REST at sub-second cadence.

## Current stack (Inst++ — implemented)

```
Matchbook REST / API-Football / optional Odds API (OFF by default)
        │
        ▼
Python worker — async tiered scheduler (250ms tick)
        │  circuit breakers + hourly API budgets (Redis-shared)
        ▼
Redis — ZSET tick rings (peak window) + snapshot keys
        │  pub/sub line_bus (fve:bus:lines:{fixture})
        ▼
FastAPI — REST /lines /arb /value-scan
        │  WebSocket /ws/lines/{fixture_key}
        ▼
Streamlit UI (or Hibs Bet via FVE_API_URL)
```

**Design rules already in place:**

- UI never calls book APIs directly in production mode.
- Odds API feed disabled unless `ENABLE_ODDS_API_FEED=1` (slow poll, default 300s).
- Shared keys protected by `FVE_*_MAX_CALLS_PER_HOUR` counters in Redis.
- Sharp de-vig + hallucination filter before value picks.

## Amended Institutional++ diagram (realistic phases)

### Phase 0 — Now (Python, good enough for value + arb research)

```
[ Book REST APIs ]
        │
        ▼
[ Python ingest worker ] ──budget gates──► Redis (ZSET + pub/sub JSON)
        │
        ▼
[ FastAPI + WS hub ] ──► [ Streamlit / external frontends ]
        │
        └──► [ arb_worker — dry-run default, £2 stake rails ]
```

### Phase 1 — Inst++ Lite (implemented)

| Item | Env | Status |
|------|-----|--------|
| Delta WS payloads | `FVE_WS_DELTA_UPDATES=1` | `line_update` with `mode=delta` + `changed_markets` |
| Fast serde | `FVE_BUS_CODEC=json\|msgpack` | orjson on cache + pub/sub (stdlib fallback) |
| WS backpressure | `WS_MAX_PENDING_SENDS=8` | Slow clients disconnected (HTTP 1013) |
| Dragonfly drop-in | `COMPOSE_PROFILES=dragonfly` | `redis://` compatible — see docker-compose |
| TCP_NODELAY + split processes | `FVE_TCP_NODELAY=1` | Redis sockets; api/worker/ui separate services |

### Phase 1.5 — Client merge + ops (implemented)

| Item | Env / path | Status |
|------|------------|--------|
| WS client delta merge | `FVE_WS_CLIENT_DELTA=0` | Hub expands bus deltas to full `lines` by default |
| Merge library | `pipeline/ws_lines_merge.py` | Python + JS (`patches/.../fve_ws_lines.js`) |
| WS telemetry | `/health` → `ws` | `active_clients`, `backpressure_drops`, `bus_messages_per_sec_60s` |
| Protobuf wire contract | `proto/market_tick.proto` | Phase 2 prep — mirrors `PriceTick` |
| Hibs upstream unpause | `scripts/vps_unpause_fve_hibs_upstream.sh` | FVE_PAUSED=0 + `FVE_UPSTREAM_MODE=hibs` |
| Matchbook poll tuning | `FEED_POLL_SEC_MATCHBOOK=0.5` | Bigger win than JSON serde on hot path |
| Inst++ audit | `scripts/audit_institutional_all.sh` | FVE + football/racing evidence gates |
| Racing health schema | `docs/RACING_HEALTH_SCHEMA.md` | hibs-racing R5–R7 `/api/health` fields |

### Phase 1.6 — Separate feeds + excellence (implemented)

| Item | Env / path | Status |
|------|------------|--------|
| Separate feed chain | `FVE_FEED_MODE=separate` | `composite` feed: matchbook → odds-backup → api-football → scrape-cache |
| Scrape sidecar | `FVE_SCRAPE_LINES_URL` | HTTP JSON only — scrape risk outside FVE process |
| Hibs sports via feed | `pipeline/feed_sports.py` | No API-Football burn in `FVE_UPSTREAM_MODE=hibs` |
| Worker heartbeat | `/health` → `worker` | All ingest modes touch `FVE_WORKER_HEARTBEAT` |
| Feed chain telemetry | `/health` → `feed_chain` | `sources_tried`, `complete_1x2`, `tick_count` |
| Streamlit Inst++ scan | `app.py` | No per-fixture book API poll when Inst++ enabled |
| hibs-bet lines API | `fve_lines_proxy.py` wired in web.py | `/api/fve/lines/<fixture>` |

Do these **before** rewriting in Rust:

1. **Delta WS payloads** — send `{type, changed_markets, ts}` not full `shopped` tree every tick.
2. **orjson / msgpack** on the wire between worker and gateway (keep Python).
3. **DragonflyDB** as Redis drop-in if pub/sub volume grows.
4. **WS backpressure** — drop clients that cannot keep up (`WS_MAX_PENDING_SENDS`).
5. **Deploy tuning** — `TCP_NODELAY`, separate worker and API processes on same host.

### Phase 2 — Split gateway (only if Hibs Bet needs many concurrent WS clients)

```
[ Python worker + model ] ──Protobuf ticks──► [ Redis / NATS ]
                                                    │
                                                    ▼
                              [ Rust/Go WS gateway — binary frames only ]
                                                    │
                                    ┌───────────────┴───────────────┐
                                    ▼                               ▼
                          [ Execution bots ]              [ Web / mobile UI ]
```

- Move **only** fan-out and framing to Rust/Actix or Go.
- Keep de-vig, Poisson model, and arb logic in Python until profiling proves otherwise.
- Protobuf schema can mirror `pipeline.tick.PriceTick` — no need for a new domain model.

### Phase 3 — True low-latency (only for automated execution at scale)

| Blueprint item | When it applies | FVE default |
|----------------|-----------------|-------------|
| Kernel bypass / DPDK | Colocated matching engine, µs arb | **Skip** |
| Shared memory / Aeron IPC | Same-host worker+gateway, Redis CPU-bound | Phase 2+ only |
| Lock-free LMAX ring | >10k msgs/s single process | **Skip** |
| CPU isolation / C-states off | Dedicated trading metal | VPS: not worth it |
| Binary WS to browsers | Mobile app or custom terminal | Phase 2 optional |

## Serialization guidance

The sample `market_tick.proto` is fine as a **wire contract** between worker and gateway.
Amendments for this codebase:

- Use `string fixture_key` (not `uint32 fixture_id`) — human labels are first-class here.
- Add `string bookmaker`, `string source`, `map<string,string> meta` for bet URLs / runner ids.
- Keep `shin_fair_prob` on a separate `FairLine` message — computed once per fixture, not per tick.

Browsers can stay on JSON WebSocket for Streamlit; binary frames are for service-to-service.

## WebSocket usage

```bash
uvicorn api.main:app --port 8000
# Connect: ws://localhost:8000/ws/lines/Arsenal%20v%20Chelsea
# Messages: snapshot | update | waiting | pong
# Client text: ping | snapshot
```

Worker and API must share `REDIS_URL` for cross-process updates.

## API budget env vars (shared across repos)

```bash
FVE_MATCHBOOK_MAX_CALLS_PER_HOUR=1200    # default
FVE_ODDS_API_MAX_CALLS_PER_HOUR=15       # conservative — shared key
FVE_API_FOOTBALL_MAX_CALLS_PER_HOUR=100
FVE_BUDGET_PREFIX=fve:budget             # isolate per product if needed
```

Check `/health` → `api_budgets` for live counters.

## Product boundary

- **FVE** — research, line shop, sharp benchmark, optional Matchbook arb (dry-run).
- **Hibs Bet** — separate product; consume FVE via `FVE_API_URL` / WS, do not duplicate feeds.

Do not merge stacks without an explicit decision — shared **Redis budget prefix** and **keys**
are enough to coordinate quota across repos.
