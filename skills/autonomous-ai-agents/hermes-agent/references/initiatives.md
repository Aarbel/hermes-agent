# Initiatives

Standing multi-session objectives. A thin harness brick *above* `/goal`,
kanban, and cron — not a replacement for them.

Docs: `website/docs/user-guide/features/initiatives.md`

## When to use

A durable north star that should notice events and then spawn an existing
brick (`/goal` for a same-session loop, `hermes kanban create` for a card,
`hermes cron` for a schedule). Not for "keep going in this chat" — that is
`/goal`.

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

## Compose

After a wake, pick an existing brick instead of inventing a planner:

- one chat, iterate until done → `/goal`
- many tasks / workers → `hermes kanban create`
- on a clock → `hermes cron`
