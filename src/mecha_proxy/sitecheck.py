"""Check that Bandai's site still looks the way the scraper expects.

Run weekly by .github/workflows/site-check.yml (and by hand with `mecha-proxy site-check`).
It reads every card list page, a few card details, a few images and the scenario list, and
reports anything that no longer parses. Nothing is downloaded in bulk: ~20-30 requests total.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, config, scenarios, scraper

SAMPLES = 3          # card details / images checked per card type
MAX_DROP = 0.20      # a count falling by more than this since last time is reported
PEEK_BYTES = 1024

# Fields a parsed card must have, per type. "other" cards vary (tokens, markers), so any one will do.
REQUIRED_ALL = {"unit": ("hp", "vp", "tl"), "tactics": ("effect",)}
REQUIRED_ANY = {"other": ("hp", "effect", "abilities")}


@dataclass
class Result:
    problems: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    baseline: dict[str, int] = field(default_factory=dict)
    checked_at: str = ""

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def fingerprint(self) -> str:
        """Stable id for this set of problems, so a repeat of the same failure isn't re-reported."""
        return hashlib.sha256("\n".join(sorted(self.problems)).encode()).hexdigest()[:12]


def _short(exc: BaseException, limit: int = 200) -> str:
    msg = " ".join(f"{type(exc).__name__}: {exc}".split())
    return msg if len(msg) <= limit else msg[:limit] + "…"


def _samples(items: list, n: int = SAMPLES) -> list:
    """First, middle and last (or fewer): spreads the check across sets without randomness."""
    if len(items) <= n:
        return list(items)
    step = (len(items) - 1) / (n - 1)
    return [items[round(i * step)] for i in range(n)]


def _peek(cache, url: str) -> tuple[int, str, bytes]:
    """GET just the first bytes of a file: (status, content type, head)."""
    cache._throttle()
    resp = cache.session.get(url, timeout=config.TIMEOUT_S, stream=True)
    try:
        head = b""
        if resp.status_code < 400:
            head = next(resp.iter_content(chunk_size=PEEK_BYTES), b"")
        return resp.status_code, (resp.headers.get("Content-Type") or "").lower(), head
    finally:
        resp.close()


def _check_details(cache, ctype: config.CardType, card: scraper.Card) -> str | None:
    try:
        d = scraper.parse_modal(cache.get_text(scraper.modal_url(ctype, card.code)))
    except Exception as exc:  # noqa: BLE001 - every failure becomes a report line
        return f"{ctype.key} {card.code}: card details didn't load or parse ({_short(exc)})"
    if not isinstance(d.get("traits"), list):
        return f"{ctype.key} {card.code}: card details are missing the traits section"
    missing = [f for f in REQUIRED_ALL.get(ctype.key, ()) if d.get(f) in (None, "", [])]
    if missing:
        return f"{ctype.key} {card.code}: card details are missing {', '.join(missing)}"
    any_of = REQUIRED_ANY.get(ctype.key)
    if any_of and not any(d.get(f) not in (None, "", []) for f in any_of):
        return f"{ctype.key} {card.code}: card details have none of {', '.join(any_of)}"
    return None


def _check_image(cache, ctype: config.CardType, card: scraper.Card) -> str | None:
    try:
        status, kind, head = _peek(cache, card.print_image)
    except Exception as exc:  # noqa: BLE001
        return f"{ctype.key} {card.code}: image request failed ({_short(exc)})"
    if status >= 400:
        return f"{ctype.key} {card.code}: image returned HTTP {status} ({card.print_image})"
    if not kind.startswith("image/") or not head:
        return f"{ctype.key} {card.code}: image URL didn't return an image ({kind or 'no content type'})"
    return None


def _check_cards(cache, ctype: config.CardType, result: Result, log) -> None:
    try:
        cards = scraper.scrape_type(cache, ctype, log=log)
    except Exception as exc:  # noqa: BLE001
        result.problems.append(f"{ctype.key}: card list didn't load or parse ({_short(exc)})")
        return
    result.counts[ctype.key] = len(cards)
    for card in _samples(cards):
        for check in (_check_details, _check_image):
            problem = check(cache, ctype, card)
            if problem:
                result.problems.append(problem)


def _check_scenarios(cache, result: Result, log) -> None:
    try:
        scs = scenarios.fetch_list(cache)
    except Exception as exc:  # noqa: BLE001
        result.problems.append(f"scenarios: list page didn't load ({_short(exc)})")
        return
    if not scs:
        result.problems.append("scenarios: list page parsed to 0 scenarios - the page layout may have changed")
        return
    available = [s for s in scs if s.available]
    result.counts["scenarios"] = len(available)
    log(f"scenarios: {len(scs)} listed, {len(available)} available")
    for sc in available:
        try:
            status, _, head = _peek(cache, sc.url)
        except Exception as exc:  # noqa: BLE001
            result.problems.append(f"scenario {sc.title}: PDF request failed ({_short(exc)})")
            continue
        if status >= 400:
            result.problems.append(f"scenario {sc.title}: PDF returned HTTP {status} ({sc.url})")
        elif not head.startswith(b"%PDF"):
            result.problems.append(f"scenario {sc.title}: link no longer points to a PDF ({sc.url})")


def _check_drops(result: Result) -> None:
    for key, before in sorted(result.baseline.items()):
        now = result.counts.get(key)
        if now is None or not isinstance(before, int) or before <= 0:
            continue  # a failed list is already reported above
        if now < before * (1 - MAX_DROP):
            result.problems.append(f"{key}: count dropped from {before} to {now} since the last good check "
                                   "(part of the list may no longer be read)")


def run(cache, baseline: dict | None = None, log=print) -> Result:
    result = Result(baseline={k: v for k, v in (baseline or {}).items() if isinstance(v, int)},
                    checked_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    for ctype in config.CARD_TYPES.values():
        _check_cards(cache, ctype, result, log)
    _check_scenarios(cache, result, log)
    _check_drops(result)
    return result


def load_baseline(path: Path | None) -> dict:
    if not path or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data.get("counts", {}) if isinstance(data, dict) else {}


def save_counts(result: Result, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"counts": result.counts, "checked_at": result.checked_at}, indent=1),
                    encoding="utf-8")


def report(result: Result) -> str:
    """Markdown for the GitHub issue. The fingerprint comment lets the workflow skip repeats."""
    lines = ["## Bandai site check " + ("passed" if result.ok else "failed"), "",
             f"Checked {result.checked_at} with Mecha-Assemble-Proxy-Helper {__version__}.", ""]
    if result.problems:
        lines += ["**Problems**", ""] + [f"- {p}" for p in result.problems] + [""]
    keys = sorted(set(result.counts) | set(result.baseline))
    if keys:
        lines += ["| | last good check | now |", "|---|---|---|"]
        lines += [f"| {k} | {result.baseline.get(k, '–')} | {result.counts.get(k, 'failed')} |" for k in keys]
        lines.append("")
    lines.append(f"<!-- site-check:{result.fingerprint if result.problems else 'ok'} -->")
    return "\n".join(lines) + "\n"
