"""PyInstaller entry point for the command-line tool."""
import sys

from mecha_proxy.cli import main

sys.exit(main())
