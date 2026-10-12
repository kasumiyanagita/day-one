"""進捗の保存と、復習の時期の管理。

保存先は `$XDG_DATA_HOME/dayone/progress.json`(既定では ~/.local/share/dayone/)。
環境変数 DAYONE_DATA_DIR があればそのディレクトリを使う。

復習は「定着度」が上がるほど間隔を空ける方式。お題をヒントなし・間違いなしで解けたら
定着度が 1 つ上がり、そうでなければ最初に戻って翌日また出題する。
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

SAVE_VERSION = 1
# 定着度ごとの、次の復習までの日数
INTERVALS = (1, 3, 7, 14, 30)
MAX_LEVEL = len(INTERVALS) - 1


def today() -> date:
    """今日の日付。DAYONE_TODAY=2026-10-12 のように指定すると差し替えられる(動作確認用)。"""
    if override := os.environ.get("DAYONE_TODAY"):
        return date.fromisoformat(override)
    return date.today()


def data_dir() -> Path:
    if override := os.environ.get("DAYONE_DATA_DIR"):
        return Path(override)
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "dayone"


def default_path() -> Path:
    return data_dir() / "progress.json"


@dataclass
class CardRecord:
    learned: str  # 初めて正解した日
    level: int  # 定着度 (0 〜 MAX_LEVEL)
    due: str  # 次に復習する日
    reviews: int = 0  # 覚えたあとに正解した回数

    def is_due(self, on: date) -> bool:
        return date.fromisoformat(self.due) <= on

    def days_until_due(self, on: date) -> int:
        return (date.fromisoformat(self.due) - on).days


@dataclass(frozen=True)
class Recorded:
    """正解を記録した結果。表示用。"""

    first_time: bool
    clean: bool
    level: int
    days: int  # 次の復習までの日数


@dataclass
class Progress:
    path: Path
    cards: dict[str, CardRecord] = field(default_factory=dict)
    # チュートリアルを見せたか(初めて起動したときに 1 回だけ自動で出すため)
    tutorial_seen: bool = False
    # 読み込めなかったセーブデータを退避した先(あれば起動時に知らせる)
    quarantined: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> Progress:
        path = path or default_path()
        if not path.exists():
            return cls(path)
        try:
            data = json.loads(path.read_text())
            if data["version"] != SAVE_VERSION:
                raise ValueError(f"unsupported save version: {data['version']}")
            cards = {card_id: CardRecord(**record) for card_id, record in data["cards"].items()}
            for record in cards.values():
                date.fromisoformat(record.learned), date.fromisoformat(record.due)
                if not 0 <= record.level <= MAX_LEVEL:
                    raise ValueError(f"invalid level: {record.level}")
            return cls(path, cards, tutorial_seen=bool(data.get("tutorial_seen", False)))
        except (OSError, ValueError, KeyError, TypeError):
            # 壊れたデータを上書きして消さないよう、別名で残してから新しく始める
            quarantined = path.with_name(f"{path.name}.broken-{int(time.time())}")
            path.rename(quarantined)
            return cls(path, quarantined=quarantined)

    def save(self) -> None:
        data = {
            "version": SAVE_VERSION,
            "tutorial_seen": self.tutorial_seen,
            "cards": {card_id: dataclasses.asdict(record) for card_id, record in self.cards.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 書き込み途中で落ちても元のファイルが壊れないよう、別ファイルに書いてから置き換える
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, self.path)

    def mark_tutorial_seen(self) -> None:
        """チュートリアルを見せたことを記録して保存する。"""
        if not self.tutorial_seen:
            self.tutorial_seen = True
            self.save()

    def is_learned(self, card_id: str) -> bool:
        return card_id in self.cards

    def due(self, card_ids: list[str], on: date) -> list[str]:
        """復習の時期が来ているカード。期日が古い順(同じ日なら card_ids の順)。"""
        due = [card_id for card_id in card_ids if card_id in self.cards and self.cards[card_id].is_due(on)]
        return sorted(due, key=lambda card_id: self.cards[card_id].due)

    def record(self, card_id: str, *, clean: bool, on: date) -> Recorded:
        """正解を記録して保存する。clean はヒントなし・間違いなしで解けたかどうか。"""
        record = self.cards.get(card_id)
        first_time = record is None
        if record is None:
            record = self.cards[card_id] = CardRecord(learned=on.isoformat(), level=0, due="")
        else:
            record.reviews += 1
            record.level = min(MAX_LEVEL, record.level + 1) if clean else 0
        days = INTERVALS[record.level]
        record.due = (on + timedelta(days=days)).isoformat()
        self.save()
        return Recorded(first_time=first_time, clean=clean, level=record.level, days=days)
