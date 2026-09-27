"""Standing initiatives — a thin harness brick above goals, kanban, and cron.

An initiative is a durable, multi-session objective. It is NOT a /goal (one
session Ralph loop), NOT a kanban card (a unit of work), and NOT a cron job
(a schedule). Those bricks are how an initiative *moves*; this brick is the
north star that decides *whether* to wake at all.

Token posture (the whole point):

- System prompt carries at most a few one-line headlines (empty → zero tokens).
- Full body is loaded on demand via ``hermes initiative show`` / ``read_file``.
- Webhook/cron events hit ``consider`` first: string match, no LLM. Unmatched
  events print ``[SILENT]`` and the webhook adapter drops them for free.
- Matched events spawn a dedicated wake prompt with that one initiative — they
  never mutate the user's live conversation, so the prompt cache stays intact.
- No new core model tool. The agent shells this CLI (or the user uses
  ``/initiative``), same footprint as ``hermes cron`` / ``hermes kanban``.

Store (profile-aware via ``get_hermes_home()``)::

    ~/.hermes/initiatives/<slug>.md          # YAML frontmatter + markdown body
    ~/.hermes/initiatives/<slug>.notes.jsonl # append-only log, not in the prompt
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from hermes_constants import display_hermes_home, get_hermes_home


STATUSES = ("active", "paused", "done", "archived")
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
_DEFAULT_MAX_INDEX = 5
_DEFAULT_HEADLINE_MAX = 80
_WAKE_BODY_CHARS = 4000
_FINGERPRINT_CHARS = 8000
_SILENT = "[SILENT]"

_WEBHOOK_SCRIPT = '''\
#!/usr/bin/env python3
"""Webhook filter: wake Hermes only when an active initiative matches.

Point a webhook route's ``script:`` at this file. Unmatched events print
``[SILENT]`` and cost zero model tokens.
"""
from hermes_cli.initiatives import consider_main

if __name__ == "__main__":
    consider_main()
'''


# ---------------------------------------------------------------------------
# Paths & time
# ---------------------------------------------------------------------------


def initiatives_dir(home: Optional[Path] = None) -> Path:
    base = Path(home) if home is not None else get_hermes_home()
    return base / "initiatives"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_name(name: str) -> str:
    slug = (name or "").strip().lower()
    if not _NAME_RE.match(slug):
        raise ValueError(
            "initiative name must be a slug: start with a letter, then "
            "lowercase letters/digits/hyphens, max 40 chars "
            f"(got {name!r})"
        )
    return slug


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass
class Initiative:
    name: str
    headline: str
    status: str = "active"
    body: str = ""
    watch: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    path: Optional[Path] = None

    def to_frontmatter(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "headline": self.headline,
            "status": self.status,
            "watch": list(self.watch),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _clip_headline(text: str, limit: int = _DEFAULT_HEADLINE_MAX) -> str:
    line = " ".join((text or "").strip().split())
    if len(line) <= limit:
        return line
    return line[: max(0, limit - 1)].rstrip() + "…"


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    content = text[1:] if text.startswith("\ufeff") else text
    if not content.startswith("---"):
        return {}, content
    end = re.search(r"\n---\s*\n", content[3:])
    if not end:
        return {}, content
    raw = content[3 : end.start() + 3]
    body = content[end.end() + 3 :]
    try:
        import yaml

        parsed = yaml.safe_load(raw) or {}
        if isinstance(parsed, dict):
            return parsed, body
    except Exception:
        parsed = {}
        for line in raw.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            parsed[key.strip()] = value.strip()
        return parsed, body
    return {}, body


def _dump_markdown(init: Initiative) -> str:
    import yaml

    fm = yaml.safe_dump(
        init.to_frontmatter(),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    body = (init.body or "").strip()
    return f"---\n{fm}---\n\n{body}\n"


def _coerce_watch(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
        return [p for p in parts if p]
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            s = str(item).strip()
            if s:
                out.append(s)
        return out
    return []


def _from_file(path: Path) -> Initiative:
    text = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    name = str(fm.get("name") or path.stem).strip().lower()
    status = str(fm.get("status") or "active").strip().lower()
    if status not in STATUSES:
        status = "active"
    headline = _clip_headline(str(fm.get("headline") or name))
    return Initiative(
        name=name,
        headline=headline,
        status=status,
        body=body.strip(),
        watch=_coerce_watch(fm.get("watch")),
        created_at=str(fm.get("created_at") or ""),
        updated_at=str(fm.get("updated_at") or ""),
        path=path,
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def list_initiatives(
    *,
    include_inactive: bool = False,
    home: Optional[Path] = None,
) -> list[Initiative]:
    folder = initiatives_dir(home)
    if not folder.is_dir():
        return []
    found: list[Initiative] = []
    for path in sorted(folder.glob("*.md")):
        try:
            init = _from_file(path)
        except Exception:
            continue
        if not include_inactive and init.status != "active":
            continue
        found.append(init)
    return found


def get_initiative(name: str, *, home: Optional[Path] = None) -> Initiative:
    slug = _validate_name(name)
    path = initiatives_dir(home) / f"{slug}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no initiative named {slug!r}")
    return _from_file(path)


def save_initiative(init: Initiative, *, home: Optional[Path] = None) -> Path:
    from utils import atomic_write_text

    slug = _validate_name(init.name)
    init.name = slug
    init.headline = _clip_headline(init.headline)
    if init.status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    now = _now()
    if not init.created_at:
        init.created_at = now
    init.updated_at = now
    path = initiatives_dir(home) / f"{slug}.md"
    atomic_write_text(path, _dump_markdown(init))
    init.path = path
    return path


def set_status(name: str, status: str, *, home: Optional[Path] = None) -> Initiative:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    init = get_initiative(name, home=home)
    init.status = status
    save_initiative(init, home=home)
    return init


def append_note(name: str, text: str, *, home: Optional[Path] = None) -> Path:
    from utils import atomic_write_text

    init = get_initiative(name, home=home)
    folder = initiatives_dir(home)
    path = folder / f"{init.name}.notes.jsonl"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    line = json.dumps({"ts": _now(), "text": text.strip()}, ensure_ascii=False)
    atomic_write_text(path, existing + line + "\n")
    return path


def read_notes(name: str, *, last: int = 10, home: Optional[Path] = None) -> list[dict[str, Any]]:
    slug = _validate_name(name)
    path = initiatives_dir(home) / f"{slug}.notes.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows[-last:] if last else rows


# ---------------------------------------------------------------------------
# Prompt index (session-start, cache-safe, empty → "")
# ---------------------------------------------------------------------------


def _index_config(home: Optional[Path] = None) -> tuple[bool, int]:
    """Return (prompt_index_enabled, max_index). Fail closed to defaults."""
    enabled = True
    max_index = _DEFAULT_MAX_INDEX
    try:
        from hermes_cli.config import load_config

        cfg = (load_config() or {}).get("initiatives") or {}
        if isinstance(cfg, dict):
            if cfg.get("enabled") is False:
                return False, max_index
            if cfg.get("prompt_index") is False:
                return False, max_index
            raw = cfg.get("max_index", max_index)
            try:
                max_index = max(0, int(raw))
            except (TypeError, ValueError):
                pass
    except Exception:
        pass
    return enabled, max_index


def build_prompt_index(
    *,
    home: Optional[Path] = None,
    max_index: Optional[int] = None,
) -> str:
    """One-line headlines for the system prompt. Empty when nothing is active.

    Baked at session start only — never mutated mid-conversation — so the
    cached prefix stays valid. Full bodies stay on disk.
    """
    enabled, cfg_max = _index_config(home)
    if not enabled:
        return ""
    cap = cfg_max if max_index is None else max_index
    if cap <= 0:
        return ""
    active = list_initiatives(home=home)[:cap]
    if not active:
        return ""
    lines = [
        "## Initiatives",
        "Standing objectives for this profile. Load one with "
        "`hermes initiative show <name>` (or `read_file` on its markdown) "
        "before acting on it. Do not restate them unless the user's request "
        "is about one. Wake on matching events via `hermes initiative consider`.",
    ]
    for init in active:
        watch = f"  [watch: {', '.join(init.watch)}]" if init.watch else ""
        lines.append(f"- {init.name}: {init.headline}{watch}")
    extra = len(list_initiatives(home=home)) - len(active)
    if extra > 0:
        lines.append(
            f"({extra} more active — `hermes initiative list` to see them; "
            "they are not in this prompt on purpose.)"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Event matching — string match first, LLM never
# ---------------------------------------------------------------------------


def _walk_strings(value: Any, blob: list[str], budget: int) -> None:
    if sum(len(s) for s in blob) >= budget:
        return
    if isinstance(value, str):
        text = value.strip()
        if text and len(text) < 500:
            blob.append(text.lower())
        return
    if isinstance(value, dict):
        for key, item in value.items():
            blob.append(str(key).lower())
            _walk_strings(item, blob, budget)
        return
    if isinstance(value, (list, tuple)):
        for item in value[:30]:
            _walk_strings(item, blob, budget)


def event_fingerprint(event: dict[str, Any]) -> str:
    """Flatten an event payload into a cheap searchable blob.

    GitHub/GitLab/generic webhooks and cron ticks all go through here.
    Nested objects are walked; long strings are skipped. No model call.
    """
    blob: list[str] = []
    _walk_strings(event, blob, _FINGERPRINT_CHARS)
    # Common header-ish aliases the webhook adapter may stash on the payload.
    for key in ("event", "event_type", "x-github-event", "x-gitlab-event"):
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            blob.append(val.strip().lower())
    seen: set[str] = set()
    ordered: list[str] = []
    for token in blob:
        if token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return " ".join(ordered)[:_FINGERPRINT_CHARS]


def watcher_matches(watch: Iterable[str], fingerprint: str) -> bool:
    haystack = fingerprint.lower()
    for raw in watch:
        token = str(raw).strip().lower()
        if not token:
            continue
        # "github.pull_request" matches "pull_request" + "github" both present,
        # or the dotted form itself.
        if token in haystack:
            return True
        parts = [p for p in re.split(r"[./:]+", token) if p]
        if parts and all(p in haystack for p in parts):
            return True
    return False


def matching_initiatives(
    event: dict[str, Any],
    *,
    home: Optional[Path] = None,
) -> list[Initiative]:
    fingerprint = event_fingerprint(event)
    matched: list[Initiative] = []
    for init in list_initiatives(home=home):
        if not init.watch:
            continue  # no watchers → never auto-wake
        if watcher_matches(init.watch, fingerprint):
            matched.append(init)
    return matched


def build_wake_prompt(
    inits: list[Initiative],
    event: dict[str, Any],
    *,
    home: Optional[Path] = None,
) -> str:
    """Prompt for a *new* session — not injected into the user's live chat."""
    blocks: list[str] = []
    names = ", ".join(i.name for i in inits)
    title = f"initiative:{inits[0].name}" if inits else "initiative"
    blocks.append(
        f"[Initiative wake: {names}]\n"
        "An external event matched one or more standing initiatives. "
        f"Name this session `{title}` with `/title {title}` before doing work "
        "(usage is billed per session; filter later with "
        f"`hermes sessions list --title {title}`). "
        "Take the next concrete step, or hand the work to an existing brick "
        "(`/goal` for a same-session loop, `hermes kanban create` for a card, "
        "`hermes cron` for a schedule). If you spawn `delegate_task`, child "
        "token use is on those child sessions — `/agents` shows the live "
        "subtree. If the event is not actually relevant, say so in one "
        "sentence and stop. Do not invent work."
    )
    for init in inits:
        body = (init.body or "").strip()
        if len(body) > _WAKE_BODY_CHARS:
            body = (
                body[:_WAKE_BODY_CHARS]
                + f"\n\n[truncated — `hermes initiative show {init.name}` for the rest]"
            )
        watch = ", ".join(init.watch) if init.watch else "(none)"
        notes = read_notes(init.name, last=5, home=home)
        note_lines = ""
        if notes:
            note_lines = "\nRecent notes:\n" + "\n".join(
                f"- {n.get('ts', '')}: {n.get('text', '')}" for n in notes
            )
        blocks.append(
            f"## {init.name}\n"
            f"Headline: {init.headline}\n"
            f"Watch: {watch}\n\n"
            f"{body or '(no body yet — fill this in if the objective needs a definition of done.)'}"
            f"{note_lines}"
        )
    # Keep the event small: fingerprint-sized JSON, not the raw dump.
    try:
        event_json = json.dumps(event, ensure_ascii=False, default=str)
    except TypeError:
        event_json = str(event)
    if len(event_json) > 2000:
        event_json = event_json[:2000] + "…"
    blocks.append(f"## Event\n```json\n{event_json}\n```")
    return "\n\n".join(blocks)


def consider_event(
    event: dict[str, Any],
    *,
    home: Optional[Path] = None,
) -> dict[str, Any] | None:
    """Return a webhook-script payload, or None to stay silent."""
    matched = matching_initiatives(event, home=home)
    if not matched:
        return None
    wake = build_wake_prompt(matched, event, home=home)
    title = f"initiative:{matched[0].name}"
    return {
        "initiative": matched[0].name,
        "initiatives": [i.name for i in matched],
        "headline": matched[0].headline,
        "session_title": title,
        "wake_prompt": wake,
        "script_output": wake,
    }


def consider_main(argv: Optional[list[str]] = None) -> int:
    """stdin JSON → stdout JSON wake payload or ``[SILENT]``.

    Designed as a webhook route script and as ``hermes initiative consider``.
    """
    parser = argparse.ArgumentParser(prog="hermes initiative consider")
    parser.add_argument(
        "--event-json",
        default="-",
        help="Path to a JSON event, or '-' for stdin (default)",
    )
    args = parser.parse_args(argv)
    if args.event_json == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(args.event_json).read_text(encoding="utf-8")
    raw = (raw or "").strip()
    if not raw:
        print(_SILENT)
        return 0
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        print(_SILENT)
        return 0
    if not isinstance(event, dict):
        print(_SILENT)
        return 0
    payload = consider_event(event)
    if payload is None:
        print(_SILENT)
        return 0
    print(json.dumps(payload, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_list(rows: list[Initiative], *, show_all: bool) -> int:
    if not rows:
        hint = " (including inactive)" if show_all else ""
        print(f"no initiatives{hint}.  hermes initiative add NAME --headline \"...\"")
        return 0
    width = max(len(i.name) for i in rows)
    for init in rows:
        watch = f"  watch={','.join(init.watch)}" if init.watch else ""
        print(f"{init.status:8s}  {init.name:<{width}}  {init.headline}{watch}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    rows = list_initiatives(include_inactive=bool(args.all))
    if args.json:
        print(json.dumps([i.to_frontmatter() | {"body": i.body} for i in rows], indent=2))
        return 0
    return _print_list(rows, show_all=bool(args.all))


def _cmd_add(args: argparse.Namespace) -> int:
    name = _validate_name(args.name)
    try:
        get_initiative(name)
        print(f"initiative {name!r} already exists — use `hermes initiative show {name}`")
        return 1
    except FileNotFoundError:
        pass
    headline = (args.headline or "").strip() or name.replace("-", " ")
    watch = _coerce_watch(args.watch)
    body = (args.body or "").strip()
    if args.file:
        body = Path(args.file).read_text(encoding="utf-8")
    init = Initiative(name=name, headline=headline, body=body, watch=watch)
    path = save_initiative(init)
    shown = display_hermes_home()
    print(f"⊙ initiative {name} created  ({shown}/initiatives/{path.name})")
    print(f"  {init.headline}")
    if watch:
        print(f"  watch: {', '.join(watch)}")
    else:
        print("  (no watchers — this one will not auto-wake; add with --watch)")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    init = get_initiative(args.name)
    print(f"name:      {init.name}")
    print(f"status:    {init.status}")
    print(f"headline:  {init.headline}")
    print(f"watch:     {', '.join(init.watch) if init.watch else '(none)'}")
    print(f"updated:   {init.updated_at or '—'}")
    if init.path:
        print(f"file:      {init.path}")
    print()
    print(init.body or "(empty body)")
    notes = read_notes(init.name, last=10)
    if notes:
        print("\n## Log")
        for note in notes:
            print(f"- {note.get('ts', '')}: {note.get('text', '')}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    init = set_status(args.name, args.status)
    print(f"⊙ {init.name} → {init.status}")
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    init = get_initiative(args.name)
    added = _coerce_watch(args.tokens)
    if args.clear:
        init.watch = []
    elif args.remove:
        drop = {t.strip().lower() for t in added}
        init.watch = [w for w in init.watch if w.lower() not in drop]
    else:
        seen = {w.lower() for w in init.watch}
        for token in added:
            if token.lower() not in seen:
                init.watch.append(token)
                seen.add(token.lower())
    save_initiative(init)
    print(f"⊙ {init.name} watch: {', '.join(init.watch) or '(none)'}")
    return 0


def _cmd_note(args: argparse.Namespace) -> int:
    text = " ".join(args.text).strip()
    if not text:
        print("note text is required")
        return 1
    append_note(args.name, text)
    print(f"⊙ noted on {args.name}")
    return 0


def _cmd_consider(args: argparse.Namespace) -> int:
    argv = ["--event-json", args.event_json]
    return consider_main(argv)


def _cmd_index(args: argparse.Namespace) -> int:
    text = build_prompt_index()
    if not text:
        print("(no active initiatives — prompt index is empty, zero tokens)")
        return 0
    print(text)
    return 0


def _cmd_install_script(args: argparse.Namespace) -> int:
    from utils import atomic_write_text

    dest = get_hermes_home() / "scripts" / "initiative-consider.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(dest, _WEBHOOK_SCRIPT)
    shown = display_hermes_home()
    print(f"⊙ wrote {shown}/scripts/initiative-consider.py")
    print("Point a webhook route at it:")
    print()
    print("  hermes webhook subscribe initiative-wake \\")
    print("    --script initiative-consider.py \\")
    print('    --prompt "{wake_prompt}"')
    print()
    print("Unmatched events stay [SILENT] — no model tokens.")
    print("On a match, consider JSON includes session_title=initiative:<slug>;")
    print("the wake prompt asks the agent to /title the new session that way")
    print("so hermes sessions list --title can cost it.")
    return 0


def register_cli(parent: argparse.ArgumentParser) -> None:
    parent.set_defaults(func=lambda a: (parent.print_help(), 0)[1])
    subs = parent.add_subparsers(dest="initiative_command")

    p_list = subs.add_parser("list", aliases=["ls"], help="List initiatives")
    p_list.add_argument("--all", action="store_true", help="Include paused/done/archived")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=_cmd_list)

    p_add = subs.add_parser("add", help="Create an initiative")
    p_add.add_argument("name", help="Slug (lowercase, hyphens ok)")
    p_add.add_argument("--headline", required=True, help="One sentence. Shown in the prompt index.")
    p_add.add_argument(
        "--watch",
        action="append",
        default=[],
        help="Event token that may auto-wake this initiative. Repeatable. "
             "Examples: github.pull_request, push:main, cron:weekly",
    )
    p_add.add_argument("--body", default="", help="Markdown body (definition of done, constraints)")
    p_add.add_argument("--file", help="Read body from a markdown file")
    p_add.set_defaults(func=_cmd_add)

    p_show = subs.add_parser("show", help="Print the full initiative (body + recent notes)")
    p_show.add_argument("name")
    p_show.set_defaults(func=_cmd_show)

    for status, help_text in (
        ("pause", "Stop auto-waking without deleting"),
        ("resume", "Mark active again"),
        ("done", "Mark finished"),
        ("archive", "Hide from the default list"),
    ):
        p = subs.add_parser(status, help=help_text)
        p.add_argument("name")
        target = {"pause": "paused", "resume": "active"}.get(status, status)
        p.set_defaults(func=_cmd_status, status=target)

    p_watch = subs.add_parser("watch", help="Add/remove auto-wake tokens")
    p_watch.add_argument("name")
    p_watch.add_argument("tokens", nargs="*", help="Tokens to add (default) or remove")
    p_watch.add_argument("--remove", action="store_true", help="Remove the given tokens")
    p_watch.add_argument("--clear", action="store_true", help="Drop every watcher")
    p_watch.set_defaults(func=_cmd_watch)

    p_note = subs.add_parser("note", help="Append a log line (not injected into the prompt)")
    p_note.add_argument("name")
    p_note.add_argument("text", nargs="+")
    p_note.set_defaults(func=_cmd_note)

    p_consider = subs.add_parser(
        "consider",
        help="Match a JSON event against active watchers. Prints [SILENT] or a wake payload. Zero LLM.",
    )
    p_consider.add_argument("--event-json", default="-", help="Path or '-' for stdin")
    p_consider.set_defaults(func=_cmd_consider)

    p_index = subs.add_parser("prompt-index", help="Print the system-prompt headlines (debug)")
    p_index.set_defaults(func=_cmd_index)

    p_script = subs.add_parser(
        "install-script",
        help="Write ~/.hermes/scripts/initiative-consider.py for webhook routes",
    )
    p_script.set_defaults(func=_cmd_install_script)


def cli_main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hermes initiative",
        description="Standing initiatives — durable objectives that wake on events, not every turn.",
    )
    register_cli(parser)
    args = parser.parse_args(argv)
    fn = getattr(args, "func", None)
    if fn is None:
        parser.print_help()
        return 0
    try:
        return int(fn(args) or 0)
    except (FileNotFoundError, ValueError) as exc:
        print(f"initiative: {exc}")
        return 1


def run_slash(rest: str) -> str:
    """Execute ``/initiative …`` and return captured stdout/stderr."""
    import shlex

    tokens = shlex.split(rest) if rest and rest.strip() else []
    if not tokens or tokens[0] in {"help", "--help", "-h", "?"}:
        return (
            "Initiatives — durable objectives. Headlines sit in the prompt; "
            "bodies stay on disk; events wake them without an LLM pre-check.\n\n"
            "/initiative list [--all]\n"
            "/initiative add NAME --headline \"...\" [--watch token]\n"
            "/initiative show NAME\n"
            "/initiative pause|resume|done|archive NAME\n"
            "/initiative watch NAME TOKEN [--remove|--clear]\n"
            "/initiative note NAME TEXT\n"
            "/initiative consider   (stdin JSON → [SILENT] or wake payload)\n"
        )
    buf_out = io.StringIO()
    buf_err = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            rc = cli_main(tokens)
    except SystemExit as exc:
        out = buf_out.getvalue().rstrip()
        err = buf_err.getvalue().rstrip()
        if exc.code in {0, None} and out:
            return out
        body = err or out
        return f"⚠ /initiative usage error\n{body}" if body else "⚠ /initiative usage error"
    out = buf_out.getvalue().rstrip()
    err = buf_err.getvalue().rstrip()
    body = "\n".join(p for p in (out, err) if p)
    if rc and not body:
        return "⚠ /initiative failed"
    return body


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli_main())
