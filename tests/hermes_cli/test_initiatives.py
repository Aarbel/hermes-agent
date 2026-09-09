"""Behavior tests for standing initiatives.

Contracts, not snapshots:
- Active headlines can appear in a prompt index; bodies do not.
- Empty store / disabled config → empty index (zero tokens).
- Event matching is string-only; unmatched events stay silent.
- Persistence is profile-home files, not ~/.hermes hardcoded.
"""

from __future__ import annotations

import io
import json
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from hermes_cli import initiatives as ini


@pytest.fixture
def init_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    import hermes_constants
    import importlib

    importlib.reload(hermes_constants)
    importlib.reload(ini)
    monkeypatch.setattr(ini, "_index_config", lambda home=None: (True, ini._DEFAULT_MAX_INDEX))
    return home


def _add(home, name, headline, *, watch=None, body="", status="active"):
    item = ini.Initiative(
        name=name,
        headline=headline,
        watch=list(watch or []),
        body=body,
        status=status,
    )
    ini.save_initiative(item, home=home)
    return item


def test_add_list_show_roundtrip(init_home):
    _add(
        init_home,
        "ship-v2",
        "Ship dashboard v2 to production.",
        watch=["github.pull_request"],
        body="Done means the release tag is cut.",
    )
    rows = ini.list_initiatives(home=init_home)
    assert len(rows) == 1
    assert rows[0].name == "ship-v2"
    assert rows[0].watch == ["github.pull_request"]
    shown = ini.get_initiative("ship-v2", home=init_home)
    assert "release tag" in shown.body
    assert shown.headline == "Ship dashboard v2 to production."


def test_paused_hidden_from_default_list(init_home):
    _add(init_home, "alpha", "Alpha headline")
    _add(init_home, "beta", "Beta headline", status="paused")
    names = [i.name for i in ini.list_initiatives(home=init_home)]
    assert names == ["alpha"]
    all_names = [i.name for i in ini.list_initiatives(include_inactive=True, home=init_home)]
    assert all_names == ["alpha", "beta"]


def test_prompt_index_is_headlines_only(init_home):
    _add(
        init_home,
        "ship-v2",
        "Ship dashboard v2.",
        watch=["github.pull_request"],
        body="This long body must never leak into the system prompt index.\n" * 20,
    )
    text = ini.build_prompt_index(home=init_home)
    assert "## Initiatives" in text
    assert "ship-v2: Ship dashboard v2." in text
    assert "never leak" not in text
    assert "github.pull_request" in text


def test_prompt_index_empty_when_none_active(init_home):
    assert ini.build_prompt_index(home=init_home) == ""
    _add(init_home, "old", "Finished work", status="done")
    assert ini.build_prompt_index(home=init_home) == ""


def test_prompt_index_omitted_when_disabled(init_home, monkeypatch):
    _add(init_home, "ship-v2", "Ship v2")
    monkeypatch.setattr(ini, "_index_config", lambda home=None: (False, 5))
    assert ini.build_prompt_index(home=init_home) == ""


def test_prompt_index_respects_max_index_cap(init_home):
    for i in range(7):
        _add(init_home, f"item-{i}", f"Headline {i}")
    text = ini.build_prompt_index(home=init_home, max_index=3)
    assert text.count("- item-") == 3
    assert "more active" in text


def test_headline_is_clipped(init_home):
    long = "x" * 200
    _add(init_home, "wordy", long)
    item = ini.get_initiative("wordy", home=init_home)
    assert len(item.headline) <= ini._DEFAULT_HEADLINE_MAX
    assert item.headline.endswith("…")


def test_invalid_name_rejected(init_home):
    with pytest.raises(ValueError):
        ini.save_initiative(ini.Initiative(name="has spaces", headline="x"), home=init_home)
    with pytest.raises(ValueError):
        ini.save_initiative(ini.Initiative(name="123nope", headline="x"), home=init_home)
    with pytest.raises(ValueError):
        ini.save_initiative(ini.Initiative(name="", headline="x"), home=init_home)


def test_consider_silent_when_no_watchers_or_no_match(init_home):
    _add(init_home, "quiet", "No auto wake")  # no watchers
    _add(init_home, "prs", "Review inbound PRs", watch=["github.pull_request"])
    assert ini.consider_event({"action": "push", "ref": "refs/heads/main"}, home=init_home) is None
    assert ini.matching_initiatives({"action": "push"}, home=init_home) == []


def test_consider_matches_dotted_watcher_without_llm(init_home):
    _add(
        init_home,
        "prs",
        "Review inbound PRs",
        watch=["github.pull_request"],
        body="Leave a review comment if tests look thin.",
    )
    event = {
        "event": "pull_request",
        "action": "opened",
        "repository": {"full_name": "nous/hermes-agent"},
        "pull_request": {"title": "fix the thing", "html_url": "https://github.com/nous/hermes-agent/pull/1"},
    }
    payload = ini.consider_event(event, home=init_home)
    assert payload is not None
    assert payload["initiative"] == "prs"
    assert payload["session_title"] == "initiative:prs"
    assert "Initiative wake" in payload["wake_prompt"]
    assert "/title initiative:prs" in payload["wake_prompt"]
    assert "Leave a review comment" in payload["wake_prompt"]


def test_consider_github_event_type_on_payload(init_home):
    _add(init_home, "prs", "Review PRs", watch=["pull_request"])
    payload = ini.consider_event(
        {"event_type": "pull_request", "action": "synchronize"},
        home=init_home,
    )
    assert payload is not None
    assert payload["initiatives"] == ["prs"]


def test_consider_main_empty_stdin_is_silent(init_home, capsys, monkeypatch):
    monkeypatch.setattr(ini.sys, "stdin", io.StringIO(""))
    rc = ini.consider_main(["--event-json", "-"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "[SILENT]"


def test_consider_main_json_payload(init_home, tmp_path, capsys):
    _add(init_home, "prs", "Review PRs", watch=["pull_request"])
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"event": "pull_request", "action": "opened"}))
    rc = ini.consider_main(["--event-json", str(event_path)])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out != "[SILENT]"
    payload = json.loads(out)
    assert payload["initiative"] == "prs"
    assert payload["session_title"] == "initiative:prs"
    assert "wake_prompt" in payload


def test_notes_are_sidecar_not_in_index(init_home):
    _add(init_home, "ship-v2", "Ship v2")
    ini.append_note("ship-v2", "Cut the RC tag yesterday.", home=init_home)
    notes = ini.read_notes("ship-v2", home=init_home)
    assert notes[-1]["text"] == "Cut the RC tag yesterday."
    index = ini.build_prompt_index(home=init_home)
    assert "RC tag" not in index
    md = (init_home / "initiatives" / "ship-v2.md").read_text()
    assert "RC tag" not in md


def test_pause_resume_done(init_home):
    _add(init_home, "ship-v2", "Ship v2")
    ini.set_status("ship-v2", "paused", home=init_home)
    assert ini.get_initiative("ship-v2", home=init_home).status == "paused"
    ini.set_status("ship-v2", "active", home=init_home)
    assert ini.list_initiatives(home=init_home)[0].status == "active"
    ini.set_status("ship-v2", "done", home=init_home)
    assert ini.build_prompt_index(home=init_home) == ""


def test_cli_add_and_list(init_home, capsys):
    rc = ini.cli_main([
        "add", "ship-v2",
        "--headline", "Ship dashboard v2.",
        "--watch", "github.pull_request",
        "--body", "Cut the tag.",
    ])
    assert rc == 0
    rc = ini.cli_main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ship-v2" in out
    assert "Ship dashboard v2." in out


def test_slash_help_does_not_raise():
    text = ini.run_slash("")
    assert "/initiative list" in text
    assert "consider" in text


def test_install_script_writes_under_hermes_home(init_home):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ini._cmd_install_script(Namespace())
    assert rc == 0
    path = init_home / "scripts" / "initiative-consider.py"
    assert path.is_file()
    content = path.read_text()
    assert "consider_main" in content
    assert str(Path.home() / ".hermes" / "scripts") not in content


def test_fingerprint_is_bounded():
    huge = {"blob": "a" * 50_000, "event": "push"}
    fp = ini.event_fingerprint(huge)
    assert len(fp) <= ini._FINGERPRINT_CHARS
    assert "push" in fp
