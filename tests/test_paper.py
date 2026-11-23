"""A4 paper support alongside US Letter."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from mecha_proxy import backs, cli, config, pdf, scenarios, service
from mecha_proxy.layout import fit_grid

from .test_scenarios import make_spread

A4_PT = (595, 842)
LETTER_PT = (612, 792)


def _size_pt(path: Path, index: int = 0) -> tuple[int, int]:
    box = PdfReader(str(path)).pages[index].mediabox
    return round(float(box.width)), round(float(box.height))


def test_paper_lookup():
    assert config.paper_mm("a4") == (210.0, 297.0)
    assert config.paper_mm("letter") == config.LETTER_MM
    assert config.paper_mm(None) == config.paper_mm("bogus") == config.LETTER_MM  # unknown: Letter
    assert config.paper_label("a4") == "A4" and config.paper_label(None) == "US Letter"
    assert service.PrintSettings().paper == "letter"


@pytest.mark.parametrize("paper,card,bleed,expected", [
    ("a4", "unit", 0, 2), ("a4", "unit", 3, 2), ("a4", "tactics", 0, 9), ("a4", "tactics", 3, 8),
    ("letter", "unit", 0, 2), ("letter", "tactics", 0, 9), ("letter", "tactics", 3, 6),
])
def test_cards_per_sheet(paper, card, bleed, expected):
    s = service.PrintSettings(card_type=card, bleed=bleed, paper=paper)
    assert service.per_sheet(s) == expected


def test_card_pdf_and_calibration_use_paper(tmp_path):
    fronts = []
    for i in range(3):
        f = tmp_path / f"f{i}.png"
        Image.new("RGB", (746, 1044), (200, 20 * i, 90)).save(f)
        fronts.append(f)
    back = backs.back_path(tmp_path, 63, 88, 0, dpi=60)
    job = pdf.PrintJob(fronts=fronts, labels=["A", "B", "C"], card_w=63, card_h=88, back=back,
                       paper=config.A4_MM)
    out = pdf.build(job, "duplex", tmp_path / "a4.pdf", tmp_path, log=lambda *a: None)[0]
    assert _size_pt(out) == _size_pt(out, 1) == A4_PT
    cal = service.make_calibration(service.PrintSettings(card_type="tactics", paper="a4"), tmp_path / "cal.pdf")
    assert _size_pt(cal) == A4_PT
    cal = service.make_calibration(service.PrintSettings(card_type="tactics"), tmp_path / "cal2.pdf")
    assert _size_pt(cal) == LETTER_PT


def test_scenarios_on_a4_are_bigger(tmp_path):
    src = make_spread(tmp_path / "spread.pdf")
    a4, notes = scenarios.build_pdf([src], tmp_path / "a4.pdf", goal_cards=True, log=lambda m: None,
                                    paper="a4")
    assert not notes and len(PdfReader(str(a4)).pages) == 3
    assert all(_size_pt(a4, i) == A4_PT for i in range(3))
    letter, _ = scenarios.build_pdf([src], tmp_path / "l.pdf", log=lambda m: None)
    assert _size_pt(letter) == LETTER_PT
    # an A3 spread splits into two A4 halves: only the 5 mm margin shrinks it (~95% vs ~91% on Letter)
    half = (420 / 2, 297)
    a4_scale = min((210 - 10) / half[0], (297 - 10) / half[1])
    letter_scale = min((215.9 - 10) / half[0], (279.4 - 10) / half[1])
    assert a4_scale == pytest.approx(0.952, abs=0.002) and letter_scale == pytest.approx(0.907, abs=0.002)


def test_cli_paper_option(tmp_path, capsys):
    out = tmp_path / "cal.pdf"
    assert cli.main(["--home", str(tmp_path / "home"), "calibrate", "--paper", "a4",
                     "-o", str(out)]) == 0
    assert _size_pt(out) == A4_PT
    with pytest.raises(SystemExit):
        cli.main(["calibrate", "--paper", "legal"])


def test_a4_grid_fits_inside_margins():
    for ct in config.CARD_TYPES.values():
        g = fit_grid(*config.A4_MM, *ct.size_mm)
        assert g.x0 >= 5 - 1e-9 and g.y0 >= 5 - 1e-9
