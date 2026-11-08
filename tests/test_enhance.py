"""AI enhancement engine tests, using a stand-in upscaler (a script with the real tool's name/CLI)."""
from __future__ import annotations

import hashlib
import io
import json
import stat
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from mecha_proxy import enhance, service
from mecha_proxy.cache import Cache
from mecha_proxy.scraper import Card

FAKE_UPSCALER = f"""#!{sys.executable}
import sys
from PIL import Image
a = sys.argv[1:]
src, dst, scale = a[a.index("-i") + 1], a[a.index("-o") + 1], int(a[a.index("-s") + 1])
if __import__("os").environ.get("FAKE_GPU_FAIL"):
    sys.stderr.write("vkCreateInstance failed -9\\n"); sys.exit(255)
im = Image.open(src)
im.resize((im.width * scale, im.height * scale)).save(dst)
"""


def install_fake_tools(meta: Path) -> enhance.Tools:
    if sys.platform == "win32":  # the stand-in is a script named .exe, which Windows can't run directly
        pytest.skip("stand-in upscaler needs a POSIX system (runs in CI/Linux)")
    tools = enhance.Tools(meta)
    tools.models.mkdir(parents=True, exist_ok=True)
    tools.exe.write_text(FAKE_UPSCALER)
    tools.exe.chmod(tools.exe.stat().st_mode | stat.S_IEXEC)
    for name in ("ultramix-balanced-4x", "realesr-animevideov3-x2"):
        for ext in ("param", "bin"):
            (tools.models / f"{name}.{ext}").write_bytes(b"x")
    tools.marker.write_text("{}")
    return tools


@pytest.fixture
def lib(tmp_path):
    cache = Cache(tmp_path, delay_s=0, log=lambda *a: None)
    url = "https://example.test/img_card_front_st01-u01_en.webp"
    card = Card("st01-u01", "unit", "st01", "Sample", url, url)
    src = cache.local_path(url, card.image_relpath(url))
    src.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 567), (200, 30, 40)).save(src, "WEBP")
    return cache, card, src


def test_enhance_store_and_skip(lib):
    cache, card, src = lib
    tools = install_fake_tools(cache.meta_dir)
    assert tools.installed("ultramix") and tools.installed("official")
    store = service.open_store(cache)
    done, skipped, failed = enhance.enhance_many(store, tools, [(card, src)], "ultramix", False)
    assert (done, skipped, failed) == (1, 0, [])
    master = store.path(card)
    assert master.suffix == ".webp" and master.parent == cache.root / "cards-enhanced" / "unit" / "ST01"
    with Image.open(master) as im:
        assert im.size == (3200, 2268)
    assert src.read_bytes() and Image.open(src).size == (800, 567)  # original untouched

    store2 = service.open_store(cache)  # index persists
    assert enhance.enhance_many(store2, tools, [(card, src)], "ultramix", False)[:2] == (0, 1)
    # preset change or a changed original -> redone
    assert enhance.enhance_many(store2, tools, [(card, src)], "official", False)[0] == 1
    with Image.open(store2.path(card)) as im:
        assert im.size == (1600, 1134)  # x2 preset
    Image.new("RGB", (800, 567), (10, 10, 200)).save(src, "WEBP")
    assert enhance.enhance_many(store2, tools, [(card, src)], "official", False)[0] == 1


def test_space_saving_and_print_sizes(lib, tmp_path):
    cache, card, src = lib
    tools = install_fake_tools(cache.meta_dir)
    store = service.open_store(cache)
    enhance.enhance_many(store, tools, [(card, src)], "ultramix", False)
    work = cache.processed_dir

    p400 = store.for_print(card, 400, work)
    assert Image.open(p400).width == enhance.print_width_px("unit", 400) == 2331
    assert store.for_print(card, None, work) == store.path(card)  # "max" = the master itself
    small = store.for_print(card, 400, work, jpeg=True)  # "Smaller PDF"
    assert small.suffix == ".jpg" and Image.open(small).width == 2331

    webp = store.path(card)
    assert store.compress(card)
    jpg = store.path(card)
    assert jpg.suffix == ".jpg" and not webp.exists()
    with Image.open(jpg) as im:
        assert im.width == enhance.print_width_px("unit", enhance.SPACE_SAVING_DPI)
    assert store.for_print(card, 300, work) == jpg  # compressed copies are used as-is
    assert not store.compress(card)  # already compressed
    # compressed + new enhancements respect space saving
    assert enhance.enhance_many(store, tools, [(card, src)], "ultramix", True)[:2] == (0, 1)
    # turning space saving off: full quality replaces the JPEG
    assert enhance.enhance_many(store, tools, [(card, src)], "ultramix", False)[0] == 1
    assert store.path(card).suffix == ".webp" and not jpg.exists()


def test_pdf_embeds_space_saving_jpeg_unchanged(lib):
    from mecha_proxy import pdf
    cache, card, src = lib
    tools = install_fake_tools(cache.meta_dir)
    store = service.open_store(cache)
    enhance.enhance_many(store, tools, [(card, src)], "ultramix", True)
    jpg = store.path(card)
    assert pdf.prepare_image(jpg, 148, 105, 0, 0, cache.processed_dir) == jpg  # passthrough
    bled = pdf.prepare_image(jpg, 148, 105, 2, 0, cache.processed_dir)
    assert bled.suffix == ".jpg" and bled != jpg  # bleed needs a new image, still JPEG
    s = service.PrintSettings(card_type="unit", mode="single", enhanced=True)
    out = service.make_pdf(cache, [(card, 1)], {card.code: src}, s, cache.root / "e.pdf",
                           log=lambda m: None, store=store)[0]
    assert b"/DCTDecode" in out.read_bytes()  # the JPEG went in as JPEG, not re-encoded lossless


def test_falls_back_to_original_without_enhanced(lib):
    cache, card, src = lib
    s = service.PrintSettings(card_type="unit", mode="single", enhanced=True)
    msgs = []
    out = service.make_pdf(cache, [(card, 1)], {card.code: src}, s, cache.root / "o.pdf", log=msgs.append)
    assert out[0].exists() and any("0 of 1" in m for m in msgs)


def test_gpu_failure_message(lib, monkeypatch):
    cache, card, src = lib
    tools = install_fake_tools(cache.meta_dir)
    monkeypatch.setenv("FAKE_GPU_FAIL", "1")
    with pytest.raises(enhance.EnhanceError, match="Vulkan"):
        enhance.enhance_many(service.open_store(cache), tools, [(card, src)], "ultramix", False)


class _Resp:
    def __init__(self, data):
        self.data, self.headers = data, {"Content-Length": str(len(data))}

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        for i in range(0, len(self.data), n):
            yield self.data[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Sess:
    def __init__(self, routes):
        self.routes, self.headers = routes, {}

    def get(self, url, stream=False, timeout=None):
        return _Resp(self.routes[url])


def test_install_verifies_checksums(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("realesrgan-ncnn-vulkan.exe", b"exe")
        z.writestr("vcomp140.dll", b"dll")
        z.writestr("models/realesr-animevideov3-x2.param", b"p")
        z.writestr("models/realesr-animevideov3-x2.bin", b"b")
        z.writestr("onepiece_demo.mp4", b"not needed")
    data = buf.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    tools = enhance.Tools(tmp_path, engine=enhance.ENGINES["windows"])
    seen = []
    tools.install(["official"], session=_Sess({"u": data}), zip_url="u", zip_sha=sha,
                  on_progress=lambda *a: seen.append(a))
    assert tools.installed("official") and not tools.installed("ultramix")
    assert not (tools.dir / "onepiece_demo.mp4").exists()
    assert "Kim2091" in (tools.dir / "THIRD-PARTY-LICENSES.txt").read_text()
    assert seen

    bad = enhance.Tools(tmp_path / "bad", engine=enhance.ENGINES["windows"])
    with pytest.raises(enhance.EnhanceError, match="checksum"):
        bad.install(["official"], session=_Sess({"u": data}), zip_url="u", zip_sha="0" * 64)
    assert not bad.exe.exists()

    # a tampered model file is refused too
    p = enhance.PRESETS["ultramix"]
    routes = {f[1]: b"tampered" for f in p.files}
    with pytest.raises(enhance.EnhanceError, match="checksum"):
        tools.install(["ultramix"], session=_Sess(routes), zip_url="u", zip_sha=sha)
    assert not tools.installed("ultramix")


def test_platform_engines(monkeypatch):
    import platform as pf
    cases = [("win32", "AMD64", "windows"), ("darwin", "arm64", "macos"), ("darwin", "x86_64", "macos"),
             ("linux", "x86_64", "linux"), ("linux", "aarch64", None), ("freebsd14", "amd64", None)]
    for plat, machine, key in cases:
        monkeypatch.setattr(sys, "platform", plat)
        monkeypatch.setattr(pf, "machine", lambda m=machine: m)
        assert enhance.platform_key() == key, (plat, machine)
        assert enhance.supported() == (key is not None)
    for key, eng in enhance.ENGINES.items():  # every pinned build: official release, fixed checksum
        assert eng.url.startswith("https://github.com/xinntao/Real-ESRGAN/releases/download/")
        assert len(eng.sha256) == 64 and eng.exe.endswith(".exe") == (key == "windows")


@pytest.mark.skipif(sys.platform == "win32", reason="Unix permissions")
def test_install_unix_engine_sets_exec_bit(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:  # like the real macOS/Ubuntu zips: no Unix permissions stored
        z.writestr("realesrgan-ncnn-vulkan", b"#!/bin/sh\n")
        z.writestr("models/realesr-animevideov3-x2.param", b"p")
        z.writestr("models/realesr-animevideov3-x2.bin", b"b")
        z.writestr("README_ubuntu.md", b"readme")
        z.writestr("../evil", b"x")             # never extracted
        z.writestr("sub/dir/other", b"x")       # nothing outside models/ subfolders either
    data = buf.getvalue()
    eng = enhance.Engine("u", hashlib.sha256(data).hexdigest(), 1, "realesrgan-ncnn-vulkan")
    tools = enhance.Tools(tmp_path, engine=eng)
    tools.install(["official"], session=_Sess({"u": data}))
    assert tools.installed("official")
    assert tools.exe.stat().st_mode & stat.S_IXUSR
    assert not (tmp_path / "evil").exists() and not (tools.dir / "sub").exists()
    assert not (tools.dir / "README_ubuntu.md").exists()
    marker = json.loads(tools.marker.read_text())
    assert marker["sha256"] == eng.sha256


def test_missing_linux_library_message(lib, monkeypatch):
    cache, card, src = lib
    tools = install_fake_tools(cache.meta_dir)
    tools.exe.write_text("#!/bin/sh\necho 'realesrgan-ncnn-vulkan: error while loading shared libraries: "
                         "libvulkan.so.1: cannot open shared object file' >&2\nexit 127\n")
    with pytest.raises(enhance.EnhanceError, match="libvulkan.so.1.*apt install"):
        enhance.enhance_many(service.open_store(cache), tools, [(card, src)], "ultramix", False)


def test_cli_enhance_skips_types_not_downloaded(lib, capsys):
    """Only units downloaded (no 'other' list yet): the default --type unit,other still works."""
    from dataclasses import asdict

    from mecha_proxy import cli
    cache, card, src = lib
    install_fake_tools(cache.meta_dir)
    cache.save_index()
    cache.index[card.print_image] = {"file": card.image_relpath(card.print_image),
                                     "sha256": hashlib.sha256(src.read_bytes()).hexdigest()}
    cache.save_index()
    cache.save_manifest({"cards": {"unit": [asdict(card)]}})
    assert cli.main(["--home", str(cache.root), "enhance", "--cards", "st01-u01"]) == 0
    out = capsys.readouterr().out
    assert "skipping other: not downloaded yet" in out and "1 enhanced" in out
    with pytest.raises(SystemExit, match="unknown card type"):
        cli.main(["--home", str(cache.root), "enhance", "--type", "units"])
