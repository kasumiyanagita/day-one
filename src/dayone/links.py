"""障害対応ゲーム oncall との対応(oncall.toml)の読み込み。"""

from __future__ import annotations

import functools
import tomllib
from dataclasses import dataclass
from pathlib import Path

LINKS_FILE = Path(__file__).parent / "oncall.toml"


@dataclass(frozen=True)
class OncallLinks:
    ready: tuple[str, ...]  # oncall を始める前に覚えておきたいカードの id
    missions: tuple[tuple[str, tuple[str, ...]], ...]  # (ミッション名, そこで使うカードの id)

    def missions_using(self, card_id: str) -> list[str]:
        return [title for title, cards in self.missions if card_id in cards]


@functools.cache
def load_links() -> OncallLinks:
    with LINKS_FILE.open("rb") as f:
        data = tomllib.load(f)
    return OncallLinks(
        ready=tuple(data["ready"]),
        missions=tuple((m["title"], tuple(m["cards"])) for m in data["missions"]),
    )
