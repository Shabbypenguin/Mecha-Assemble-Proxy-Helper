# Mecha-Assemble-Proxy-Helper

Lays out replacement **unit** and **tactics** cards for miniatures you already own
onto US Letter sheets, ready to print on cardstock at home, single- or double-sided.

> Unofficial fan tool. Not affiliated with, endorsed by, or supported by the
> publisher. It ships **no card images**: each user's copy downloads the images
> the publisher posts publicly, straight to their own computer, for personal use.
> Respect the publisher's data-use guidelines: don't share, sell, or redistribute
> the images or the PDFs you generate.

## Download

From the [Releases page](https://github.com/Shabbypenguin/Mecha-Assemble-Proxy-Helper/releases):

- **Windows** – `…-windows.zip`. Unzip it anywhere you can write to and run
  `Mecha-Assemble-Proxy-Helper.exe`. To update, unzip the new version over the old folder: your
  cards and settings (kept next to the .exe) stay.
- **macOS** (experimental, macOS 12+) – `…-macos-apple-silicon.zip` for M1/M2/M3/M4 Macs,
  `…-macos-intel.zip` for older Intel Macs. Unzip and drag the app to Applications. The app
  isn't signed by Apple, so the first launch is blocked: open **System Settings → Privacy &
  Security**, scroll down and click **Open Anyway** (once). Or, in Terminal:
  `xattr -dr com.apple.quarantine /Applications/Mecha-Assemble-Proxy-Helper.app`.
  Cards and settings are kept in `Documents/Mecha-Assemble-Proxy-Helper`, so updating is just
  replacing the app.
- **Linux** – install from source (below).

The app tells you when a new version is out (checked at most once a day; turn it off with
*Cards → Check for new cards and app updates at startup*).

## Install from source (Windows, macOS, Linux)

Requires Python 3.10+.

```
git clone https://github.com/<you>/Mecha-Assemble-Proxy-Helper && cd Mecha-Assemble-Proxy-Helper && python -m pip install .
```

## Desktop app

```
python -m pip install -e ".[gui]" && mecha-proxy-gui
```

- **Unit / Tactics / Other** tabs with thumbnail grids; hover a card to see it full size.
- **Filters** on the left: type-ahead chips for Set, Traits and Ability/Effect type (traits
  and types must *all* match), HP / VP / TL ranges and "show currently selected only".
  The Tactics tab follows the deck rules instead: **Usable by** – pick units (e.g. your deck)
  to see every tactics card at least one of them can use – and **Who can use it** – *Any
  unit*, *Signature unit only* (needs the unit with the same signature tag) or *<trait>
  units* (needs a unit with that trait, e.g. Londo Bell).
- **Search** matches everything on the card: name, code, model number, tags, weapon and
  ability names and their text (e.g. `slow`, `mass produced`).
- **Start printing…** opens print setup with a diagram for each setting and a live
  preview of the first sheet, front and back, then saves the PDF(s).
- **Update cards** (full update) re-reads the card lists and every card's details and
  downloads any new images. At startup the app only does a quick check of the list pages for
  new cards. Settings are remembered between runs.
- **Cards Bandai changed** (errata): a full update compares every card's details with the
  saved ones. Changed cards get an **↻ Updated** marker (hover it to see what changed), their
  image is re-checked. If a changed card is one you've printed before, a banner offers to select
  those cards for reprinting.
- **Deck lists**: **Export list** (above Start printing, or *Cards → Export deck list*) copies
  the selected cards from every tab to the clipboard as plain text, one `1x ST01-U02` line per
  card, ready to paste into Discord. **Import list…** opens a box to paste a shared list into;
  it replaces the current selection. It reads loose spellings
  too: `1xst01-u01`, `2 x ST01-T01`, `st01t01` (one copy), `ST01-U01 x2`, extra text and names.
- **Enhance** menu: optional AI enhancement (see below).

## Quick start (command line)

```
mecha-proxy update                                   # card list + images (downloads only what's new)
mecha-proxy list --type unit                         # see card codes
mecha-proxy calibrate --type unit -o calibrate.pdf   # test your printer's front/back alignment
mecha-proxy build --type unit --all -o units.pdf     # every unit card, duplex
```

## Choosing cards

Combine any of these:

| Option | Example | Meaning |
|---|---|---|
| `--all` | | every card of the type |
| `--sets` | `--sets st01,ex02` | whole sets |
| `--cards` | `--cards ex01-u02:3,st01-u01` | specific cards, optional `:qty` |
| `--qty` | `--qty 2` | copies of each card picked by `--all` / `--sets` |

## Print modes

| `--mode` | Output | Use when |
|---|---|---|
| `duplex` (default) | one PDF, front/back pages interleaved | your printer prints both sides automatically |
| `split` | `*_fronts.pdf` + `*_backs.pdf` | **manual duplex**: print fronts, put the stack back in flipped, print backs. **Or glue**: print each on its own sheet and glue them back to back |
| `single` | fronts only | no backs wanted |

Backs are positioned for the sheet being turned over its **long** edge. If your
printer flips on the short edge, add `--flip short`.

### Getting front and back to line up

1. Run `mecha-proxy calibrate --type unit -o cal.pdf` and print it the same way you
   will print cards (duplex, or the split/re-feed method).
2. Hold it up to a light. Outlines, crosshairs and numbers should sit on top of each other.
3. If the back is off, measure how far and pass it to `build` and `calibrate`, e.g.
   `--back-offset 1.5,-0.5` (mm, +x = right, +y = up as seen on the back page).
4. Manual duplex printing the backs in the wrong order? Add `--reverse-backs`.

Always print at **100% / Actual size** (turn off "fit to page"). Each sheet with
room has a 50 mm bar to check the scale.

## Other options

- `--bleed 2` extends each card's edge colour 2 mm past the cut line, so a slightly
  off cut doesn't leave white slivers. Fewer cards may fit per sheet.
- `--spacing 3` leaves 3 mm of white space between cards so each is cut on its own.
- `--back my_back.png` uses your own back art instead of the built-in red hex back.
- `--size 148x105` overrides the card size in mm.
- `--offline` builds using only already-downloaded images.
- `mecha-proxy back --type unit -o back.png` exports the built-in back art.

## AI enhancement (optional)

Bandai's unit images print at about 140 DPI, which looks soft on a 148 mm card. The app can
make sharper copies on your own computer with an AI upscaler. It is **off until you turn it
on** (Enhance → Enable AI enhancement…), and nothing is ever uploaded.

- **What gets downloaded:** the official [Real-ESRGAN ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN)
  release for your system (~45–50 MB) and, for the default preset, the UltraMix Balanced model
  (~33 MB) from Upscayl's repository. Each file is checked against a fixed SHA-256 before use.
  Nothing is bundled with the app. Needs a graphics card with Vulkan support (most from the last
  ~8 years; integrated Intel/AMD graphics work too, just slower).
- **Systems:** Windows; macOS (Apple Silicon and Intel – experimental); Linux on x86_64 PCs,
  with the Vulkan driver installed (Ubuntu/Debian:
  `sudo apt install libvulkan1 libgomp1 mesa-vulkan-drivers`; check with `vulkaninfo --summary`).
  Without a usable GPU the app says so instead of crashing.
- **Where it goes:** sharper copies are saved in `cards-enhanced\<type>\<SET>\` as 4× lossless
  WebP. Your originals in `cards\` are never changed.
- **Presets:** *Crisp graphics – UltraMix Balanced* (default) or *Official – Real-ESRGAN
  animevideov3 ×2*. Unit and Other cards are enhanced by default; Tactics (already ~300 DPI)
  can be ticked under Card types.
- **Enhance new cards after Update** (on by default) keeps new cards enhanced automatically.
- **Space-saving mode** stores enhanced copies as JPEG 95 % at the 400 DPI print size
  (~1 MB instead of ~5 MB each; PDFs about 3× smaller). Turning it off recreates the full
  quality copies from the originals – nothing is re-downloaded.
- **In the grid:** cards with an enhanced copy show a ✦ badge; **✦ Show enhanced** switches the
  grid and hover preview to the enhanced copies; right-click a card → *Compare original /
  enhanced* opens a slider view (scroll to zoom).
- **Printing:** while AI enhancement is on, PDFs use the enhanced copies at 400 DPI (about
  4.5 MB per unit card); cards without one use the original. Turn AI enhancement off in the
  Enhance menu to print the originals. A **Smaller PDF** tick in Start printing stores them as
  JPEG 95 % instead (about 1.1 MB per card, same look on paper).
- **Removing:** *Remove enhanced images…* deletes the copies; *Remove AI tools…* deletes the
  downloaded tools and turns AI off, but keeps the copies for when you re-enable it.

Command line: `mecha-proxy ai install`, `mecha-proxy enhance --type unit,other`,
`mecha-proxy build … --enhanced [--small-pdf]`.

Credits: Real-ESRGAN by Xintao Wang (BSD-3-Clause), ncnn by Tencent (BSD-3-Clause),
[UltraMix Balanced](https://openmodeldb.info/models/4x-UltraMix-Balanced) by Kim2091
(CC BY-NC-SA 4.0 – non-commercial use with credit), ncnn conversion by
[Upscayl](https://github.com/upscayl/upscayl).

## Downloads (portable)

Everything stays next to the tool, so you can copy or move the whole folder:

```
Mecha-Assemble-Proxy-Helper/
  cards/                      downloaded images - browse these
    unit/
      EX02/
        EX02-U03 <card name>.webp
    tactics/
      ST01/
        ST01-T01 <card name>.webp
  .cache/                     card list, download index, resized copies (safe to delete)
```

- Images are downloaded **once**. `update` only fetches cards that are new; the desktop
  app's *Update cards* also re-reads every card's details to spot changed cards.
- `update --refresh` re-checks existing images with the server using conditional
  requests: unchanged images are not downloaded again.
- A card renamed on the site has its file moved, not re-downloaded.
- `mecha-proxy open` opens the `cards` folder; `mecha-proxy cache` shows per-set counts.
- Use `--home <folder>` (or the `MECHA_PROXY_HOME` environment variable) to keep
  `cards/` and `.cache/` somewhere else. Packaged builds use the folder the .exe is in;
  a plain `pip install` uses `Documents/Mecha-Assemble-Proxy-Helper`.
- Upgrading from v0.1.0/0.1.1 moves the old hidden cache here automatically.

## Card sizes

| Type | Size used | Status |
|---|---|---|
| Unit | 148 × 105 mm (A6 landscape) | community-reported, matches image proportions; unconfirmed |
| Tactics | 63 × 88 mm | estimated from image proportions; unconfirmed |
| Other (tokens) | 88 × 63 mm | estimated from image proportions; unconfirmed |

Sizes will be updated once confirmed with a ruler. Until then, use `--size` if yours differ.

## Building the Windows app

Released zips are on the Releases page. To build one yourself (Windows, Python 3.10+), from the
project folder:

```
python -m venv .venv && .venv\Scripts\python -m pip install -e ".[dev,gui]" pyinstaller && .venv\Scripts\python -m pytest -q && .venv\Scripts\python -m PyInstaller packaging\mecha-proxy.spec --noconfirm
```

The app is built in `dist\Mecha-Assemble-Proxy-Helper\`; zip that folder to share it. Set
`MECHA_PROXY_WITH_CLI=1` before building to also include the command-line tool
(`mecha-proxy.exe`).

### Releasing

1. Change `__version__` in `src\mecha_proxy\__init__.py` (everything else – package metadata,
   the About box, the zip name – follows it) and commit.
2. Tag and push: `git tag v0.5.0 && git push origin main v0.5.0`
3. GitHub Actions runs the tests, builds the app and attaches the zip to a **draft** release
   with generated notes. Check it, then click **Publish** – only published releases are offered
   by the app's update check. A tag that doesn't match `__version__` fails the build.

The zip holds `Mecha-Assemble-Proxy-Helper.exe` and its `_internal` folder (bundled Python, Qt and
libraries); keep them together.

Unzip anywhere you can write to (Desktop, Documents, a games folder). `cards\` and `.cache\`
are created next to the .exe, so the whole folder is portable. If the folder isn't writable
(e.g. Program Files), data goes to `Documents\Mecha-Assemble-Proxy-Helper` instead.

The .exe isn't code-signed, so Windows SmartScreen may say "Windows protected your PC":
choose **More info → Run anyway**.

## Development

```
python -m pip install -e ".[dev]" && python -m pytest
```

## License

Code: GPL-3.0-or-later (see `LICENSE`). The built-in card back art is generated by
this code. Card images belong to their rights holders and are not part of this project.
