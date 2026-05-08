# Leverage Investment Analysis — managed-agent template

## Overview

Pressure-tests an LBO investment thesis before Investment Committee: capital structure, debt capacity vs. peer LBOs, leverage scenarios, covenant headroom, and ranked risks. Same source as the [`leverage-investment-analysis`](../../plugins/agent-plugins/leverage-investment-analysis) Cowork plugin — this directory is the Managed Agent cookbook for `POST /v1/agents`.

Pairs with `model-builder` (which produces the LBO file) and hands off to `pitch-agent` when the thesis becomes a deck.

## Deploy

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export CAPIQ_MCP_URL=...
../../scripts/deploy-managed-agent.sh leverage-investment-analysis
```

## Steering events

See [`steering-examples.json`](./steering-examples.json).

## Security & handoffs

Banker- or sponsor-provided LBO models are untrusted. Three-tier isolation:

| Tier | Touches untrusted model? | Tools | Connectors |
|---|---|---|---|
| **`model-reader`** | **Yes** | `Read`, `Grep` only | None |
| `scenario-runner` / Orchestrator | No | `Read`, `Grep`, `Glob`, `Agent` | capiq (read-only) |
| **`memo-writer`** (Write-holder) | No | `Read`, `Write`, `Edit` | None |

`model-reader` returns schema-validated JSON capped at 12 projection years and 16 debt tranches. `scenario-runner` benchmarks against peer LBOs and computes covenant headroom but never writes. `memo-writer` produces `./out/ic-leverage-memo-<target>.docx`.

**Handoff:** if scenarios reveal the model itself needs rebuilding (e.g., missing PIK toggle, broken returns formula), emit a `handoff_request` for `model-builder`; `scripts/orchestrate.py` routes it.

**Not guaranteed:** the output is IC pre-read material. Final go/no-go requires IC, Credit Committee, and lender-syndicate approval outside this agent.
