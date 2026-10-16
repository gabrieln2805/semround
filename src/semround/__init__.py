"""Semround: test that semantic-model conversions keep every metric's numbers."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("semround")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without install
    __version__ = "0.0.0"


def main() -> None:
    from semround.cli import run

    run()
