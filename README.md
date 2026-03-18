# 🌳 Memory Tree

> Confidence-based memory lifecycle management for [OpenClaw](https://github.com/openclaw/openclaw) agents.

Memory Tree gives your AI agent the ability to **forget** — the most human feature of memory. Knowledge that's frequently used stays fresh and green. Knowledge that's neglected slowly fades away. When it's truly stale, its essence is archived and it's removed from active memory.

**Zero cloud API calls. Zero token consumption. Zero manual maintenance.**

## Why?

OpenClaw agents store long-term memory in `MEMORY.md`. Over time, this file grows — new rules, old decisions, skill lists, API keys, todo items... everything piles up. Important knowledge gets buried under noise.

The result? Your agent becomes slower, more expensive, and more forgetful — because it's loading thousands of tokens of irrelevant context every session.

Memory Tree fixes this with a simple insight: **forgetfulness is a feature, not a bug.**

## Highlights

- 🌍 **Works everywhere** — local machine, cloud VM, WSL, Docker. No GPU required.
- 🔍 **Multi-backend search** — Ollama (free), Zhipu/OpenAI API, or keyword fallback. Auto-detected.
- 💰 **Zero token consumption** — all operations are local Python scripts, no LLM API calls
- 🔒 **Privacy-first** — with Ollama backend, your memory data never leaves your machine
- 📦 **One-command setup** — `setup` auto-detects environment, indexes memory, creates cron jobs
- 🔄 **Hands-free after install** — automatic decay and archival run on schedule

## How It Works

Every knowledge block in `MEMORY.md` gets a **confidence score** (0.0–1.0):

| Stage | Score | Meaning |
|-------|-------|---------|
| 🌱 Sprout | 0.7 | New knowledge |
| 🌿 Green | ≥0.8 | Frequently used, thriving |
| 🍂 Yellow | 0.5–0.8 | Infrequently used, decaying |
| 🍁 Dead | 0.3–0.5 | Rarely used, near archival |
| 🪨 Soil | <0.3 | Archived, essence preserved |

### Confidence Changes

| Event | Change |
|-------|--------|
| New knowledge created | Set to 0.7 |
| Found by search | +0.03 |
| Actively used | +0.08 |
| Manual confirmation | Set to 0.95 |
| Each day unaccessed (P2) | -0.008 |
| Each day unaccessed (P1) | -0.004 |
| P0 (core principles) | Never decays |

### Semantic Search

Uses local [Ollama](https://ollama.ai) embeddings (`qwen3-embedding`) for semantic search — understands meaning, not just keywords. Searches that return results automatically boost those knowledge blocks' confidence. Frequently recalled knowledge stays alive.

## Requirements

- [Python 3.8+](https://python.org) (no pip packages needed)
- At least one search backend:
  - **Ollama** (free, recommended): `ollama serve` + `ollama pull qwen3-embedding`
  - **Zhipu API**: set `ZHIPU_API_KEY` env var or configure via `config`
  - **OpenAI API**: set `OPENAI_API_KEY` env var or configure via `config`
  - **Keyword** (built-in fallback): works with zero dependencies

## Install as OpenClaw Skill

Download `memory-tree.skill` from [Releases](https://github.com/Masongmx/memory-tree/releases) and install:

```bash
openclaw skill install memory-tree.skill
```

Or clone and copy to your skills directory:

```bash
git clone https://github.com/Masongmx/memory-tree.git
cp -r memory-tree/skill/* ~/.openclaw/workspace/skills/memory-tree/
```

## Quick Start

```bash
# One-time setup (auto-creates cron jobs + first index)
python3 skills/memory-tree/scripts/memory_tree.py setup

# View memory tree health
python3 skills/memory-tree/scripts/memory_tree.py visualize

# Semantic search
python3 skills/memory-tree/scripts/memory_tree.py search "how to fetch tweets"

# Auto-decay (usually runs via cron)
python3 skills/memory-tree/scripts/memory_tree.py decay
```

## Priority Labels

Use in `MEMORY.md` section headers to control decay speed:

```markdown
## [P0] Core Principles       # Never decays
## [P1] Important Knowledge   # Slow decay (~5 months to archive)
## [P2] Daily Notes           # Fast decay (~3.5 months to archive)
```

No tag = P2 (default).

## Data Files

All data stored under `memory-tree/data/` in your workspace:

| File | Purpose |
|------|---------|
| `confidence.json` | Confidence scores and metadata per knowledge block |
| `embeddings.json` | Cached embedding vectors for semantic search |
| `archive.json` | Archived knowledge records |

## Architecture

```
memory-tree/
├── skill/                  # OpenClaw skill package
│   ├── SKILL.md
│   └── scripts/
│       └── memory_tree.py
├── core/                   # Standalone script (same file)
│   └── memory_tree.py
├── ARTICLE.md              # Introduction article (中文)
└── README.md               # This file
```

## Credit

Inspired by [Memory-Like-A-Tree](https://github.com/loryoncloud/Memory-Like-A-Tree) by [@loryoncloud](https://x.com/loryoncloud).

## License

MIT
