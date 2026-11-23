"""Help → Copy debug info: a short, private-by-default report to paste into a GitHub issue.

Only what helps diagnose a problem: app and system versions, what's downloaded, the print and AI
settings, and the recent activity log / last error. Your user name is replaced by "~" in every
path, and nothing is sent anywhere – it only goes to the clipboard (or stdout for the CLI).
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, config

ISSUE_URL = f"{config.REPO_URL}/issues/new?template=bug_report.yml"
PREF_KEYS = ("paper", "mode", "flip", "bleed", "gap", "crop", "reverse", "small_pdf", "use_custom_back",
             "ai_enabled", "ai_preset", "ai_types", "ai_space_saving", "rules_layout", "rules_mode",
             "scen_goal_cards", "auto_check", "download_types")


def _redact(text: str) -> str:
    """Replace the home folder and the user name with "~" (paths are useful, names aren't)."""
    home = str(Path.home())
    out = text.replace(home, "~").replace(home.replace("\\", "/"), "~")
    user = os.environ.get("USERNAME") or os.environ.get("USER") or Path.home().name
    if user and len(user) > 3:
        out = re.sub(re.escape(user), "<user>", out, flags=re.IGNORECASE)
    return out


def _counts(cache) -> list[str]:
    lines = []
    try:
        manifest = cache.load_manifest() or {}
        cards = manifest.get("cards", {})
        lines.append("cards: " + (", ".join(f"{t} {len(cards.get(t, []))}" for t in config.CARD_TYPES)
                                  or "none downloaded"))
        if manifest.get("updated_at") or manifest.get("built_at"):
            lines.append(f"card list from: {manifest.get('updated_at') or manifest.get('built_at')}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"cards: unreadable ({type(exc).__name__})")
    for label, module in (("scenarios", "scenarios"), ("rules", "rulesdocs"), ("maps", "maps")):
        try:
            mod = __import__(f"mecha_proxy.{module}", fromlist=["x"])
            listed = mod.saved_list(cache)
            files = mod.load_state(cache).get("files", {})
            n = sum(getattr(d, "available", True) for d in listed)
            lines.append(f"{label}: {n} listed, {len(files)} downloaded")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"{label}: unreadable ({type(exc).__name__})")
    return lines


def _ai(cache) -> str:
    try:
        from . import enhance
        engine = enhance.current_engine()
        if engine is None:
            return "AI: not supported on this system"
        tools = enhance.Tools(cache.meta_dir)
        return f"AI: engine for {enhance.platform_key()}, {'installed' if tools.installed() else 'not installed'}"
    except Exception as exc:  # noqa: BLE001
        return f"AI: unknown ({type(exc).__name__})"


def collect(cache, prefs: dict | None = None, log_lines: list[str] | None = None,
            last_error: str | None = None) -> str:
    frozen = getattr(sys, "frozen", False)
    lines = [
        "### Debug info",
        f"app: Mecha-Assemble-Proxy-Helper {__version__} ({'packaged app' if frozen else 'from source'})",
        f"system: {platform.system()} {platform.release()} ({platform.version()}), {platform.machine()}",
        f"python: {platform.python_version()}",
    ]
    try:
        from PySide6 import __version__ as qt
        lines.append(f"qt: PySide6 {qt}")
    except Exception:  # noqa: BLE001 - CLI without the GUI extra
        pass
    lines.append(f"data folder: {cache.root}")
    lines += _counts(cache)
    lines.append(_ai(cache))
    if prefs:
        shown = {k: prefs[k] for k in PREF_KEYS if k in prefs}
        lines.append("settings: " + json.dumps(shown, separators=(", ", ": ")))
    lines.append(f"collected: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    if last_error:
        lines += ["", "last error:", "```", last_error.strip()[-2000:], "```"]
    if log_lines:
        lines += ["", "recent activity:", "```", *log_lines[-40:], "```"]
    return _redact("\n".join(lines)) + "\n"
