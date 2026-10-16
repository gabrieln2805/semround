from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
TPCDS_011 = CORPUS / "upstream" / "tpcds_0.1.1.yaml"
TPCDS_020 = CORPUS / "upstream" / "tpcds_0.2.0.dev0.yaml"
SHOP = CORPUS / "traps" / "shop.yaml"

# Make tests/mutants importable as a package.
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _chdir_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding ids hash the model path relative to cwd; keep it stable across runners."""
    monkeypatch.chdir(ROOT)


@pytest.fixture
def mutant_targets():
    from mutants import ALL

    from semround import targets

    for m in ALL:
        targets.register(m.name, m)
    yield {m.name: m for m in ALL}
    for m in ALL:
        targets._REGISTRY.pop(m.name, None)
