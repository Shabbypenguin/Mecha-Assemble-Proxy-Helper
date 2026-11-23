"""Maps tab: Bandai's printable maps, one copy per paper size."""
from __future__ import annotations

from pathlib import Path

import pytest

from mecha_proxy import cli, config, maps as M
from mecha_proxy.cache import Cache

from .test_scenarios import _Sess

FIX = Path(__file__).parent / "fixtures"
LIST_URL = config.SITE_BASE + M.LIST_PATH


def make_map(path: Path, tiles: int = 4, tag: str = "") -> Path:
    from reportlab.pdfgen import canvas
    mm = 72 / 25.4
    c = canvas.Canvas(str(path), pagesize=(213.8 * mm, 185 * mm))
    for i in range(tiles):
        c.setFillColorRGB(0.6, 0.35, 0.2)
        c.rect(0, 0, 213.8 * mm, 185 * mm, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.drawString(20, 20, f"tile {i + 1} {tag}")
        c.showPage()
    c.save()
    return path


def _site(tmp_path, html: str | None = None) -> dict:
    html = html or (FIX / "maps_list.html").read_text()
    routes = {LIST_URL: html}
    for m in M.parse_list(html):
        for f in m.files.values():
            routes[f.url] = make_map(tmp_path / f"src-{f.filename}", tag=f.paper).read_bytes()
    return routes


def _cache(tmp_path, routes) -> Cache:
    return Cache(tmp_path / "home", session=_Sess(routes), delay_s=0, log=lambda *a: None)


def _quiet(*a):
    pass


def test_parse_pairs_sizes_and_skips_non_maps():
    listed = M.parse_list((FIX / "maps_list.html").read_text())
    assert [m.title for m in listed] == ["Map", "[ST01] SAMPLE ONE", "[ST01] SAMPLE TWO", "[DX01] SAMPLE THREE",
                                         "[DX01] SAMPLE FOUR"]   # the 3D-printer zip is not a map
    general, one = listed[0], listed[1]
    assert general.display == "General map" and one.display == "Sample One [ST01]"
    assert set(general.files) == {"a4", "letter"}
    # file names carry dates on some entries; the paper size comes from Bandai's note
    assert general.file("letter").filename == "261030_GASM_map_web_letter.pdf"
    assert one.file("a4").updated == "2026.11.20" and one.file("a4").size == "6.38MB"
    assert M.total_mb(listed, "letter") == 42 and M.total_mb(listed, "a4") == 38


def test_paper_from_note_or_filename():
    assert M._paper_of("Print on letter-size paper and assemble.", "x.pdf") == "letter"
    assert M._paper_of("", "https://x/GASM_map_foo_A4.pdf") == "a4"
    assert M._paper_of("", "https://x/thing.pdf") is None


def test_update_downloads_one_paper_size(tmp_path):
    routes = _site(tmp_path)
    cache = _cache(tmp_path, routes)
    listed, changed = M.update(cache, "a4", log=_quiet)
    assert len(changed) == 5
    assert all(M.local_pdf(cache, m.title, "a4") for m in listed)
    assert not any(M.local_pdf(cache, m.title, "letter") for m in listed)
    assert [m.title for m in M.saved_list(cache)] == [m.title for m in listed]
    # again: nothing new, only the list page is read
    cache.session = sess = _Sess(routes)
    assert M.update(cache, "a4", log=_quiet)[1] == [] and sess.calls == [LIST_URL]


def test_new_version_replaces_old_file(tmp_path):
    routes = _site(tmp_path)
    cache = _cache(tmp_path, routes)
    M.update(cache, "letter", log=_quiet)
    old = M.local_pdf(cache, "Map", "letter")
    html = (FIX / "maps_list.html").read_text().replace("261030_GASM_map_web_letter", "261201_GASM_map_web_letter")
    html = html.replace("2026.10.30", "2026.12.01")
    cache.session = _Sess(_site(tmp_path, html))
    _, changed = M.update(cache, "letter", log=_quiet)
    assert changed == ["Map"]
    new = M.local_pdf(cache, "Map", "letter")
    assert new.name == "261201_GASM_map_web_letter.pdf" and not old.exists()


def test_get_other_size_on_demand_and_offline(tmp_path):
    routes = _site(tmp_path)
    cache = _cache(tmp_path, routes)
    M.update(cache, "letter", log=_quiet)
    path, note = M.get(cache, "[ST01] SAMPLE ONE", "a4", log=_quiet)
    assert path.name == "GASM_map_ST01_sampleone_A4.pdf" and note.startswith("Downloaded")
    cache.session = _Sess({})  # offline
    again, note = M.get(cache, "[ST01] SAMPLE ONE", "a4", log=_quiet)
    assert again == path and "Couldn't reach" in note
    with pytest.raises(RuntimeError, match="couldn't reach"):
        M.get(cache, "[ST01] SAMPLE TWO", "a4", log=_quiet)


def test_new_on_site_and_thumbnail(tmp_path):
    routes = _site(tmp_path)
    cache = _cache(tmp_path, routes)
    assert len(M.new_on_site(cache, "letter")) == 5
    M.update(cache, "letter", log=_quiet)
    assert M.new_on_site(cache, "letter") == [] and len(M.new_on_site(cache, "a4")) == 5
    thumb = M.thumbnail(M.local_pdf(cache, "Map", "letter"), tmp_path / "t.png", width=300)
    from PIL import Image
    assert thumb and Image.open(thumb).width <= 300


def test_cli_maps(tmp_path, monkeypatch, capsys):
    routes = _site(tmp_path)
    monkeypatch.setattr("mecha_proxy.cache.requests.Session", lambda: _Sess(routes))
    monkeypatch.setattr(Cache, "_throttle", lambda self: None)
    home = str(tmp_path / "home")
    assert cli.main(["--home", home, "maps", "list"]) == 0
    assert "Sample One [ST01]" in capsys.readouterr().out
    assert cli.main(["--home", home, "maps", "get", "--map", "sample one", "--paper", "a4"]) == 0
    assert capsys.readouterr().out.strip().endswith("GASM_map_ST01_sampleone_A4.pdf")
    assert cli.main(["--home", home, "maps", "update"]) == 0
    assert "5 map(s), 5 downloaded" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="matches"):
        cli.main(["--home", home, "maps", "get", "--map", "sample"])


def test_preview_shrinks_huge_photos(tmp_path, monkeypatch):
    """A map's big terrain photo is decoded at 1/8 size for the preview only."""
    from PIL import Image
    from reportlab import rl_config
    monkeypatch.setattr(rl_config, "useA85", 0)  # plain JPEG stream, like Bandai's PDFs
    from pypdf import PdfReader
    from reportlab.pdfgen import canvas
    photo = tmp_path / "big.jpg"
    Image.new("RGB", (1600, 1400), (150, 90, 50)).save(photo, quality=80)
    src = tmp_path / "map.pdf"
    c = canvas.Canvas(str(src), pagesize=(600, 520))
    for _ in range(2):
        c.drawImage(str(photo), 0, 0, 600, 520)
        c.showPage()
    c.save()
    data = M._preview_pdf(src)
    assert isinstance(data, bytes)
    import io
    def widths(res):
        for ref in (res or {}).get("/XObject", {}).values():
            o = ref.get_object()
            yield from widths(o.get("/Resources")) if o.get("/Subtype") == "/Form" else [int(o["/Width"])]
    assert list(widths(PdfReader(io.BytesIO(data)).pages[0]["/Resources"])) == [200]
    assert M.thumbnail(src, tmp_path / "t.png", width=200)
    small = make_map(tmp_path / "small.pdf")
    assert M._preview_pdf(small) == str(small)            # nothing big: render the original
