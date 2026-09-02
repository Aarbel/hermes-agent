---
sidebar_position: 16.5
title: "Initiatives"
description: "Standing multi-session objectives. Headlines in the prompt, bodies on disk, events wake them without an LLM pre-check."
---

# Initiatives

An **initiative** is a durable objective the agent owns across sessions. It is a new brick of the Hermes harness — the same family as skills, memory, `/goal`, kanban, cron, and webhooks — and it is deliberately the *thinnest* of them.

```
Initiative   why this matters, and when to wake
    │
    ├── /goal          keep iterating in *this* chat until done
    ├── kanban         many tasks, many workers, a board
    ├── cron           wake on a clock
    ├── webhook        wake on an external event
    └── skill          how to do the work, loaded on demand
```

Hermes already had all of those bricks. What it did not have was a place to put a north star that outlives a chat, without paying for it on every turn.

## Why this exists

`/goal` is a Ralph loop inside one session. Kanban is a work queue. Cron is a schedule. Memory is who you are and what was learned. None of those is "ship v2 of the dashboard, and notice when a related PR opens."

Without a dedicated brick, people fake it by stuffing the objective into `SOUL.md` or `MEMORY.md`. That puts the full text on every API call for the life of the profile — the opposite of how Hermes treats skills (progressive disclosure) and the opposite of the prompt-cache rule (the system prompt stays small and byte-stable).

Initiatives take the skills pattern and apply it to *objectives*:

1. **Index, not body.** The system prompt may list a few one-line headlines. The markdown body stays on disk.
2. **Wake, don't poll.** External events (webhooks, cron) hit a string matcher *before* any model is called. No match → `[SILENT]` → zero tokens.
3. **Fresh session on match.** A wake does not inject into the user's live conversation. That preserves the prompt cache of the chat they are actually having.
4. **No new core tool.** The agent runs `hermes initiative …` (or `/initiative`). Same footprint as `hermes cron` and `hermes kanban`.

Simple is the design. There is no planner, no DAG, no extra judge by default.

## When to use which brick

| You want | Reach for |
|---|---|
| Keep iterating in *this* chat until a definition of done is met | [`/goal`](./goals.md) |
| Many tasks, dependencies, multiple profiles | [Kanban](./kanban.md) |
| Run something on a clock, unattended | [Cron](./cron.md) |
| Re-prompt *this* session on an interval | [`/loop`](./loops.md) / [`/heartbeat`](./heartbeat.md) |
| A long-lived objective that should notice events and spawn the bricks above | **Initiative** |

An initiative *uses* the other bricks. It does not replace them. When a matching PR opens, the wake session can set a `/goal`, create a kanban card, or just do the one step and stop.

## Quick start

```bash
hermes initiative add ship-v2 \
  --headline "Ship dashboard v2 to production." \
  --watch github.pull_request \
  --watch push:main \
  --body "Done means the release tag is cut and the changelog is published.
Constraints: do not bump the major version."
```

```
/initiative list
/initiative show ship-v2
/initiative pause ship-v2
/initiative resume ship-v2
/initiative note ship-v2 Cut the RC tag.
/initiative done ship-v2
```

Headlines of **active** initiatives (capped, default 5) appear in the system prompt at session start. Paused, done, and archived ones do not. If there are none, the prompt block is omitted — zero tokens.

## Token budget — how this stays cheap

This is the Nous-shaped part. Every path that would have cost a model call was replaced with something cheaper, in this order:

### 1. Progressive disclosure (skills pattern)

The prompt index is one line per active initiative: slug, headline, optional watchers. Bodies, notes, and history never enter the system prompt. Load them with `hermes initiative show <name>` or `read_file` on `~/.hermes/initiatives/<name>.md` (profile-aware via `get_hermes_home()`).

Notes live in a sidecar `*.notes.jsonl`, so logging progress does not rewrite the spec and does not bloat the index.

### 2. Event-first, LLM-last

```bash
echo '{"event":"push","ref":"refs/heads/main"}' | hermes initiative consider
```

- **Match** → JSON payload with a `wake_prompt` for that initiative only.
- **No match** → prints `[SILENT]`.

No auxiliary model. No judge. Watchers are substrings (and dotted tokens like `github.pull_request`, which match when every segment is present in the event). Initiatives with an empty watch list never auto-wake — they are user-invoked only.

### 3. Webhook route scripts (zero tokens on miss)

```bash
hermes initiative install-script
```

writes `~/.hermes/scripts/initiative-consider.py`. Point a webhook route at it:

```bash
hermes webhook subscribe initiative-wake \
  --script initiative-consider.py \
  --prompt "{wake_prompt}"
```

The adapter already treats `[SILENT]`, empty stdout, and `__hermes_ignore__` as "do not start an agent." Unrelated GitHub noise costs an HMAC check and a string match.

Pair this with [payload filters](../messaging/webhooks.md#payload-filters) and `events:` on the route so the script never even runs for event types you do not care about.

### 4. Cron as a cheap heartbeat, not a chat

A weekly review does not belong in `/heartbeat` (that re-enters the user's session). Use cron, and prefer a **script-only** job when you can decide without a model:

```bash
hermes cron add '0 9 * * 1' \
  --name initiative-review \
  --command 'hermes initiative list'
```

When the review actually needs a model, give it a dedicated cron session — not the user's live chat — so the conversation cache stays intact.

### 5. Prompt cache stays sacred

- The index is baked at **session start**. Adding an initiative does not rewrite the current conversation's system prompt (same rule as skills).
- Wakes go to a **new** session. They are a user-role message in that session, not a mid-loop injection in the original one.
- No toolset swap, no core tool added. `/initiative` is CLI surface, like `/kanban`.

### 6. Caps

| Knob | Default | Why |
|---|---|---|
| `initiatives.max_index` | 5 | Hard cap on headlines in the prompt. The rest stay on disk. |
| Headline length | 80 chars | One sentence. The body is for detail. |
| Wake body | 4 KB | The wake prompt truncates; `show` has the rest. |
| Event fingerprint | 8 KB | `consider` will not hash a whole clone into memory. |

```yaml
# ~/.hermes/config.yaml
initiatives:
  enabled: true
  prompt_index: true   # false = never inject headlines
  max_index: 5
```

`.env` is not involved. This is not a secret.

## What an initiative file looks like

`~/.hermes/initiatives/ship-v2.md`:

```markdown
---
name: ship-v2
headline: Ship dashboard v2 to production.
status: active
watch:
  - github.pull_request
  - push:main
created_at: 2026-09-02T15:00:00Z
updated_at: 2026-09-02T15:00:00Z
---

Done means the release tag is cut and the changelog is published.
Constraints: do not bump the major version.
```

Status is one of `active | paused | done | archived`. Only `active` is listed or indexed by default.

## Philosophy — keep it a brick, not a framework

- **Compose, don't duplicate.** Wakes should call `/goal`, kanban, or cron when those are the right shape of work. Do not reimplement a Ralph loop inside this module.
- **Fail silent.** A broken matcher or a weird payload prints `[SILENT]`. A missed wake is cheaper than a false wake that burns a session.
- **User messages always win.** An initiative never interrupts a running turn and never rewrites history.
- **Profiles are islands.** Each profile has its own `initiatives/` directory via `get_hermes_home()`. There is no live inheritance from the default profile.
- **No core tool.** If the only barrier is "the agent should call this," the agent already has `terminal`. A dedicated `initiative_*` toolset would tax every API call for users who never use this.

That last point is the footprint ladder from the contributor guide: CLI command + skill (here: the `hermes-agent` skill reference) beats a new model tool.

## Not in v1 (on purpose)

A planner that decomposes initiatives into kanban graphs. A default-on LLM relevance judge. Mid-conversation index updates. A desktop Initiatives pane. Those can layer on later without changing the file format.

If you find yourself wanting them, start with a watcher token and a webhook. Most of the value is "don't run the model until something real happened."
