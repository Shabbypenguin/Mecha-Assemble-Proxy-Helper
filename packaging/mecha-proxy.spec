# PyInstaller build of the desktop app.
#   pyinstaller packaging/mecha-proxy.spec --noconfirm
# Output: dist/Mecha-Assemble-Proxy-Helper/  (cards/ and .cache/ are created next to the .exe on first run)
#
# Also include the command-line tool (mecha-proxy.exe) by setting MECHA_PROXY_WITH_CLI=1 first:
#   PowerShell:  $env:MECHA_PROXY_WITH_CLI=1; pyinstaller packaging/mecha-proxy.spec --noconfirm
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
ICON = os.path.join(SPECPATH, "icon.ico")
APP = "Mecha-Assemble-Proxy-Helper"
WITH_CLI = os.environ.get("MECHA_PROXY_WITH_CLI", "").strip() not in ("", "0", "false", "no")
EXCLUDES = ["tkinter", "unittest", "pydoc", "numpy", "PIL.AvifImagePlugin", "PIL._avif", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick",
            "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDBus", "PySide6.QtOpenGL"]
# Files the app never uses: Qt's software-OpenGL fallback (plain widgets don't need OpenGL) and
# Pillow's AVIF codec (card images are WebP/PNG).
DROP_BINARIES = {"opengl32sw.dll"}
DROP_PREFIXES = ("_avif.",)


def keep(entries):
    return [e for e in entries
            if os.path.basename(e[0]).lower() not in DROP_BINARIES
            and not os.path.basename(e[0]).lower().startswith(DROP_PREFIXES)]


gui = Analysis([os.path.join(SPECPATH, "launch_gui.py")], pathex=[SRC],
               datas=[(ICON, ".")], excludes=EXCLUDES, noarchive=False)
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name=APP,
              console=False, icon=ICON, upx=False)
parts = [gui_exe, keep(gui.binaries), gui.datas]

if WITH_CLI:
    cli = Analysis([os.path.join(SPECPATH, "launch_cli.py")], pathex=[SRC],
                   excludes=EXCLUDES + ["PySide6", "shiboken6"], noarchive=False)
    cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="mecha-proxy",
                  console=True, icon=ICON, upx=False)
    parts += [cli_exe, keep(cli.binaries), cli.datas]

coll = COLLECT(*parts, name=APP, upx=False)
