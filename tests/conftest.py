"""Shared test setup."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_download_window(monkeypatch):
    """The "What do you want to download?" window is modal: never let it block a test.

    Tests that exercise it patch DownloadChoicesDialog.exec themselves.
    """
    try:
        from mecha_proxy import gui_choices
    except Exception:  # noqa: BLE001 - no Qt in this environment
        return
    monkeypatch.setattr(gui_choices.DownloadChoicesDialog, "exec", lambda self: 0)
