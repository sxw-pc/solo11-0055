from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stoplist_ledger.clock import FixedClock
from stoplist_ledger.compiler import compile_declaration
from stoplist_ledger.demo import register_fixtures
from stoplist_ledger.snapshot import snapshot_many
from stoplist_ledger.store import LedgerStore

FIXTURES = ROOT / "fixtures"
TEXTS = sorted((FIXTURES / "texts").glob("*.txt"))

@pytest.fixture
def clock() -> FixedClock:
    return FixedClock("2024-01-01T00:00:00Z")

@pytest.fixture
def store(tmp_path: Path) -> LedgerStore:
    return LedgerStore(tmp_path / "ledger.db").initialize()

@pytest.fixture
def populated(store: LedgerStore, clock: FixedClock) -> LedgerStore:
    register_fixtures(store, clock, "op-test-01")
    return store

def compile_fixture(store: LedgerStore, clock: FixedClock,
                    name: str = "pipeline_a.yaml") -> dict:
    return compile_declaration(store, FIXTURES / "declarations" / name, clock,
                               "op-test-01")

def snapshot_set(store: LedgerStore, clock: FixedClock,
                 compilation: dict) -> list[dict]:
    return snapshot_many(store, compilation["id"], TEXTS, clock, "op-test-01")
