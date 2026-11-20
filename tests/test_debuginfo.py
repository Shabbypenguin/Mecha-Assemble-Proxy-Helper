"""Help → Copy debug info."""
from __future__ import annotations

import json
from pathlib import Path

from mecha_proxy import cli, debuginfo
from mecha_proxy.cache import Cache


def test_collect_reports_without_personal_paths(tmp_path, monkeypatch):
    home = tmp_path / "Users" / "Jordan"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("USERNAME", "Jordan")
    monkeypatch.setenv("USER", "Jordan")
    cache = Cache(home / "Mecha", session=None, delay_s=0, log=lambda *a: None)
    cache.save_manifest({"cards": {"unit": [{"code": "st01-u01"}] * 3, "tactics": []},
                         "updated_at": "2026-11-20T10:00:00+00:00"})
    text = debuginfo.collect(cache, prefs={"paper": "a4", "ai_enabled": True, "custom_back": str(home / "x.png")},
                             log_lines=[f"12:00:0{i} step {i}" for i in range(5)],
                             last_error=f"boom\nTraceback: {home}/file.py, user jordan")
    assert "Mecha-Assemble-Proxy-Helper" in text and "python:" in text
    assert "cards: unit 3, tactics 0, other 0" in text and "card list from: 2026-11-20" in text
    assert '"paper": "a4"' in text and "custom_back" not in text  # only listed settings
    assert "data folder: ~" in text and "Jordan" not in text and "jordan" not in text
    assert "<user>" in text and "last error:" in text and "12:00:04 step 4" in text
    assert "rules: 0 listed, 0 downloaded" in text and "AI:" in text


def test_cli_debug_info(tmp_path, capsys):
    assert cli.main(["--home", str(tmp_path / "h"), "debug-info"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("### Debug info") and debuginfo.ISSUE_URL in out
    assert json.loads(json.dumps(out))  # plain text
