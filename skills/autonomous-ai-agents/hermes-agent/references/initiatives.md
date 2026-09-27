# Initiatives

Standing multi-session objectives. A thin harness brick *above* `/goal`,
kanban, and cron — not a replacement for them.

Docs: `website/docs/user-guide/features/initiatives.md`

## `/goal` vs initiative

`/goal` is a Ralph loop **inside one session**. After each turn a judge
may append a continuation user-message in *this* chat until done, paused,
blocked, or the 20-turn budget. State is JSON in `SessionDB.state_meta`
keyed `goal:<session_id>`. **There is no goal markdown file.** Inspect
with `/goal show`.

An initiative is a **profile north star**. It lives as
`$HERMES_HOME/initiatives/<slug>.md`. Headlines may appear in the system
prompt (capped); bodies stay on disk. A matching event starts a **new**
session — it does not inject into the user's live chat.

Composition: wake → optionally `/goal` (or kanban / cron) in that wake
session. Do not reimplement the Ralph loop here.

| | `/goal` | Initiative |
|---|---|---|
| Lifetime | this session | the profile |
| On disk | `state_meta` JSON, not a `.md` | `<slug>.md` + optional `*.notes.jsonl` |
| Engine | LLM judge + optional shell gates | string-match `consider`; no LLM on miss |
| Idle tokens | zero (off-prompt) | capped headlines, or omitted if empty |

## Example shapes

`/goal` (typed in chat; `/goal show` renders the contract):

```
/goal Migrate auth to JWT
verify: pytest tests/auth passes
constraints: keep the /login response shape unchanged
boundaries: only touch services/auth and its tests
stop when: a DB schema migration is required
```

Initiative file (`~/.hermes/initiatives/ship-v2.md`):

```markdown
---
name: ship-v2
headline: Ship dashboard v2 to production.
status: active
watch:
  - github.pull_request
  - push:main
---

Done means the release tag is cut and the changelog is published.
Constraints: do not bump the major version.
```

## Triggers (cron is optional)

Auto-wake = `hermes initiative consider` + JSON. Cron is one optional
clock, not the mechanism.

- **Webhook:** `install-script` + `hermes webhook subscribe … --script initiative-consider.py --prompt "{wake_prompt}"`. Pair with route `events:` / payload filters.
- **CLI:** `echo '{"event":"push"}' | hermes initiative consider` → JSON or `[SILENT]`.
- **User / agent:** `/initiative show`, empty `watch:` (never auto-wakes).
- **Prompt index:** awareness at session start, not a wake.
- **Cron:** e.g. weekly `hermes initiative list` (script-only) or `consider` against `{"event":"cron:weekly"}` if that watcher exists.
- **Hook:** shell out to `consider`; no dedicated initiative hook in v1.

Wakes title themselves `initiative:<slug>` (`/title` in the wake prompt;
`session_title` on the consider JSON).

## Commands

```
hermes initiative add NAME --headline "..." [--watch TOKEN] [--body "..."]
hermes initiative list [--all]
hermes initiative show NAME
hermes initiative pause|resume|done|archive NAME
hermes initiative watch NAME TOKEN [--remove|--clear]
hermes initiative note NAME TEXT
hermes initiative consider          # stdin JSON → [SILENT] or wake payload
hermes initiative install-script    # webhook filter under ~/.hermes/scripts/
```

In-session: `/initiative …` (same verbs). Alias `/initiatives`.

## Token rules — follow these

- Prompt index = headlines of *active* initiatives only, capped (`initiatives.max_index`, default 5). Empty store → the block is omitted (zero tokens).
- Never paste a body into the system prompt. `show` / `read_file` on demand.
- Notes are a sidecar jsonl; they are not indexed.
- Auto-wake is string matching in `consider`. No LLM pre-check. No watchers → never auto-wakes.
- Webhook route script: `hermes initiative install-script`, then `--script initiative-consider.py --prompt "{wake_prompt}"`. Unmatched events are `[SILENT]` and cost zero model tokens.
- Wakes go to a **new** session. Do not inject into the user's live chat (prompt cache).
- No `initiative_*` core tool. Use `terminal` / the CLI.

## Token tracking (no dedicated ledger)

Per initiative: title wakes `initiative:<slug>`, then:

```
hermes sessions list --title "initiative:ship-v2"
/usage                          # this session
hermes insights --days 30 --source webhook
```

SQL: `sessions.input_tokens + output_tokens` where `title LIKE 'initiative:ship-v2%'`.

Per sub-agent: `delegate_task` children are separate sessions. Parent
`/usage` does **not** include them. Live: `/agents` (TUI overlay has
per-node tokens + subtree tokens). After: child rows + `parent_session_id`.

## Compose

After a wake, pick an existing brick instead of inventing a planner:

- one chat, iterate until done → `/goal`
- many tasks / workers → `hermes kanban create`
- on a clock → `hermes cron`
