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

## `/goal` vs an initiative {#goal-vs-initiative}

These two get conflated because both are "objectives." The split is **session loop vs profile north star**.

`/goal` answers: *"in this conversation, keep taking the next step until the judge (and any quality gates) say done."*

An initiative answers: *"this objective outlives any one chat. Keep a one-line reminder in the prompt. When a matching event arrives, start a **new** session and decide whether to work."*

| | `/goal` (Ralph loop) | Initiative |
|---|---|---|
| Scope | One session. `/new` drops it; `/resume` of *that* session restores it. | The whole profile. Survives `/new`, other chats, gateway restarts. |
| On disk | **Not a markdown file.** JSON in `SessionDB.state_meta` keyed `goal:<session_id>`. Inspect with `/goal show`. | `~/.hermes/initiatives/<slug>.md` (YAML frontmatter + body) plus optional `*.notes.jsonl`. |
| Engine | After every turn, an auxiliary **judge** model (plus optional shell **gates**) decides `done` / `continue` / `blocked` / `wait`. Continuation is a user-role message in *this* chat — prompt cache stays valid. | `consider` is **string matching** on watchers. No LLM until a wake session actually starts. Empty `watch:` → never auto-wakes. |
| Default stop | `goals.max_turns` (20). `/goal pause`, user message, or judge `done`. | Status: `active` / `paused` / `done` / `archived`. A wake is one session; that session may then set a `/goal` with its own turn budget. |
| Idle cost | Zero tokens. Goal text is not in the system prompt. The judge only runs after a turn. | Capped headlines in the system prompt (`initiatives.max_index`, default 5). Bodies stay on disk. No active initiatives → the prompt block is omitted (zero tokens). |
| Where work happens | The chat you are already in. | A **fresh** session on wake (or whenever you `/initiative show` and act). Never injected mid-loop into the user's live conversation. |

Concrete composition: an initiative is the *why* and the *when to look up*. `/goal` is the *keep going in this wake until verified*. Do not reimplement the Ralph loop inside the initiative module.

### Example: what a `/goal` looks like (not a file)

There is no `~/.hermes/goals/*.md`. A completion contract is optional structured fields on the session goal. You set it with `/goal draft …` or inline `field: value` lines, and you read it with `/goal show`.

Typed into the chat:

```
/goal Migrate auth to JWT
verify: pytest tests/auth passes
constraints: keep the /login response shape unchanged
boundaries: only touch services/auth and its tests
stop when: a DB schema migration is required
```

What `/goal show` prints (the stored contract, rendered — this is the closest thing to a "goal markdown file"):

```markdown
- Outcome: Migrate auth to JWT
- Verification: pytest tests/auth passes
- Constraints: keep the /login response shape unchanged
- Boundaries: only touch services/auth and its tests
- Stop when blocked: a DB schema migration is required
```

Under the hood that is a JSON blob on `goal:<session_id>` (`outcome`, `verification`, `constraints`, `boundaries`, `stop_when`, plus turn counters, gates, subgoals). It is not loaded into other sessions. If you want the same north star in every chat, that is an initiative, not a goal.

### Example: initiative markdown file

`~/.hermes/initiatives/ship-v2.md` **is** a file. Frontmatter is the index; the body is the definition of done, loaded only when someone `show`s it or a wake includes a truncated copy:

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

When a matching PR opens, start from the diff — do not re-litigate the roadmap.
If the event is a docs-only PR, note it and stop.
```

Progress log (not in the prompt, not in the `.md`): `~/.hermes/initiatives/ship-v2.notes.jsonl`.

```json
{"ts": "2026-09-08T10:02:00Z", "text": "RC tag cut; waiting on changelog PR."}
```

Same objective, two lifetimes: keep the north star as `ship-v2.md`. When a wake (or you) actually wants to grind until CI is green, that wake session runs `/goal` with a contract like the one above.

## How an initiative wakes (cron is optional) {#triggers}

Cron is **one** optional clock. It is not the trigger system. Auto-wake is "something called `hermes initiative consider` with a JSON event." Anything that can POST or pipe JSON can wake an initiative. Unmatched events print `[SILENT]` and never start a model.

| Trigger | How it fires | Starts a model? |
|---|---|---|
| **Webhook route + consider script** | `hermes initiative install-script`, then `hermes webhook subscribe … --script initiative-consider.py --prompt "{wake_prompt}"`. GitHub/GitLab/Stripe/etc. POST to `/webhooks/<route>`. | Only on a watcher hit. HMAC + string match on a miss. |
| **Payload `events:` / `filters:`** | Route-level allow-list *before* the script runs (e.g. `events: ["pull_request"]`, [payload filters](../messaging/webhooks.md#payload-filters)). | Script never runs for other event types. |
| **stdin / file to `consider`** | `echo '{"event":"push","ref":"refs/heads/main"}' \| hermes initiative consider` or `--event-json path`. Same matcher as the webhook script. | No. Prints JSON wake payload or `[SILENT]`. You decide whether to start a session. |
| **You / the agent** | `/initiative show ship-v2`, `hermes initiative note …`, or just talking after reading the prompt index. Empty `watch:` initiatives **only** work this way. | Only if you then send a prompt. Listing and showing are local files. |
| **Prompt index** | Headlines baked into the system prompt at **session start**. | Not a wake. Awareness only. The agent may `show` the body if the user's request is related. |
| **Cron job** | Optional heartbeat, e.g. weekly `hermes initiative list` (script-only, zero LLM) or a dedicated cron session that runs `consider` against a synthetic `{"event":"cron:weekly"}` if you added that watcher. | Only if the job has a prompt / agent. Prefer `--command` / script-only for reviews that do not need a model. |
| **Gateway / plugin hook** | A hook can shell out to `hermes initiative consider` the same way a webhook script does. No special initiative hook in v1. | Same as `consider`: silent on miss. |

Rules of thumb:

- **No watchers → never auto-wakes.** User-invoked only. That is intentional, not unfinished.
- **A wake is a new session**, titled `initiative:<slug>` (the wake prompt asks the agent to `/title` it). It does not append to the chat you were having.
- **User messages always win.** An initiative never interrupts a running turn.
- `/loop` and `/heartbeat` re-enter *this* session on a timer. They are the wrong tool for a profile-level north star.

Webhook pairing (the cheap path):

```bash
hermes initiative install-script
hermes webhook subscribe initiative-wake \
  --script initiative-consider.py \
  --prompt "{wake_prompt}"
```

`consider`'s JSON includes `session_title` (`initiative:<slug>`) and `wake_prompt`. Unrelated GitHub noise dies at `[SILENT]`.

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

## Tracking tokens per initiative and per sub-agent {#token-tracking}

v1 has **no built-in per-initiative ledger**. Hermes already bills **per session** (and per child session for `delegate_task`). The trick is to make wake sessions *findable*, then sum the same columns `/usage` already writes.

### Per initiative

Wake prompts tell the agent to title the session `initiative:<slug>` (`/title initiative:ship-v2`). `consider` also returns `session_title` in its JSON. After that:

```bash
# Sessions whose title contains the slug (wake sessions + any you named by hand)
hermes sessions list --title "initiative:ship-v2"

# Same filter when pruning / exporting / costing
hermes sessions list --title "initiative:ship-v2" --min-tokens 1
hermes sessions prune --title "initiative:ship-v2" --dry-run

# Inside a live wake: this session's input/output/cache/reasoning + estimated USD
/usage

# Platform rollup (webhook wakes are source=webhook; cron jobs are source=cron)
hermes insights --days 30 --source webhook
hermes insights --days 30 --source cron
```

Dashboard: session rows show input/output tokens; [`GET /api/analytics/usage`](./web-dashboard.md) is the same store. `/status` is the in-chat snapshot.

SQL against `state.db` (see [session storage](../../developer-guide/session-storage.md#query-token-usage-statistics)) if you want a sum:

```sql
SELECT id, title, source,
       input_tokens, output_tokens,
       input_tokens + output_tokens AS total_tokens,
       estimated_cost_usd
FROM sessions
WHERE title LIKE 'initiative:ship-v2%'
ORDER BY started_at DESC;
```

`/goal` tokens are **this session's** tokens. The judge is an extra auxiliary call each continuation turn; it shows up in `/usage` for that session, not as a separate initiative line.

Idle initiative cost is the prompt-index headlines only (or zero if the store is empty / `prompt_index: false`). `consider` misses are `[SILENT]` — no tokens.

### Per sub-agent

`delegate_task` children are **their own sessions**. Only the child's final summary is appended to the parent. Parent `/usage` is the parent loop; it does not include the children's prompt tokens.

| When | Where |
|---|---|
| Live, during the turn | [`/agents`](../tui.md) (alias `/tasks`). TUI overlay: each node has input/output/reasoning tokens; **subtree tokens** is descendants on top of the node. |
| Live, CLI | `/agents` lists running processes and background delegations (api-call counts). Token rollups are the TUI overlay / session rows. |
| After the fact | Child rows in `hermes sessions list` (often titled from the child goal). Lineage is `parent_session_id` on the child → the wake or chat that spawned it. |
| Hooks | `subagent_start` / `subagent_stop` carry `parent_session_id` and `child_session_id` if you want your own meter. |

To cost "this initiative wake + everyone it spawned":

1. Find the wake: `hermes sessions list --title "initiative:ship-v2"`.
2. Sum that session's `input_tokens + output_tokens` (parent `/usage`).
3. Sum child sessions whose `parent_session_id` is that id (and their descendants, if an orchestrator spawned workers).

Do not expect the parent's `/usage` total to equal "initiative spend." Sub-agents are isolated on purpose so the parent's prompt cache stays small; the cost lives on the child rows.

## Philosophy — keep it a brick, not a framework

- **Compose, don't duplicate.** Wakes should call `/goal`, kanban, or cron when those are the right shape of work. Do not reimplement a Ralph loop inside this module. `/goal` is the in-session engine; this file is the multi-session north star. See [the comparison](#goal-vs-initiative).
- **Fail silent.** A broken matcher or a weird payload prints `[SILENT]`. A missed wake is cheaper than a false wake that burns a session.
- **User messages always win.** An initiative never interrupts a running turn and never rewrites history.
- **Profiles are islands.** Each profile has its own `initiatives/` directory via `get_hermes_home()`. There is no live inheritance from the default profile.
- **No core tool.** If the only barrier is "the agent should call this," the agent already has `terminal`. A dedicated `initiative_*` toolset would tax every API call for users who never use this.

That last point is the footprint ladder from the contributor guide: CLI command + skill (here: the `hermes-agent` skill reference) beats a new model tool.

## Not in v1 (on purpose)

A planner that decomposes initiatives into kanban graphs. A default-on LLM relevance judge. Mid-conversation index updates. A desktop Initiatives pane. Those can layer on later without changing the file format.

If you find yourself wanting them, start with a watcher token and a webhook. Most of the value is "don't run the model until something real happened."
