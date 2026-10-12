"""カードの定義(cards/*.toml)の読み込み。

1 枚のカードは 1 つの TOML ファイルで、説明・練習用のファイル・試し打ちの手順・お題からなる。
文章やファイルの中の {{名前}} は、[vars] に並べた候補からカードを開くたびに選び直す。
同じカードでも復習のたびに中身が変わり、答えの丸暗記では通らないようにするため。
"""

from __future__ import annotations

import random
import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

CARDS_DIR = Path(__file__).parent / "cards"
CHECKS = ("output", "cwd", "files", "command", "process")

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class FileSpec:
    path: str
    content: str = ""
    directory: bool = False


@dataclass(frozen=True)
class Step:
    """試し打ちの 1 手。お手本のコマンドを打つと先へ進む。"""

    say: str
    run: str
    after: str = ""
    before: str = ""  # 手順を出す前に、画面に出さずに実行しておくコマンド
    # vim のように、キー操作で結果が決まる手。お手本から正解を求めるときには実行しない
    interactive: bool = False
    # 成功とみなす終了コード。diff のように「違いがあれば 1」を返すコマンドでは (0, 1) にする
    status: tuple[int, ...] = (0,)


@dataclass(frozen=True)
class Challenge:
    ask: str
    solution: str
    check: str = "output"
    uses: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()
    done: str = ""  # コマンドの解説(名前の由来や使いどころ)。カードの最初の説明の枠に出す
    before: str = ""
    # 成功とみなす終了コード(試し打ちの status と同じ)
    status: tuple[int, ...] = (0,)
    # check = "process" のとき: この文字列をコマンドラインに含むプロセスが、すべて止まっていれば正解
    target: str = ""


@dataclass(frozen=True)
class Card:
    id: str
    command: str
    category: str
    title: str
    summary: str
    when: str
    syntax: str
    steps: tuple[Step, ...]
    challenge: Challenge
    files: tuple[FileSpec, ...] = ()
    start: str = ""  # 開始時のディレクトリ(ホームからの相対パス)
    prompt: str = "path"  # "plain" にすると、プロンプトに現在地を出さない
    vars: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def instantiate(self, rng: random.Random) -> Card:
        """{{名前}} を今回の値に置き換えたカードを返す。"""
        values = {name: rng.choice(choices) for name, choices in self.vars.items()}

        def fill(text: str) -> str:
            return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), text)

        challenge = self.challenge
        return replace(
            self,
            steps=tuple(
                replace(s, say=fill(s.say), run=fill(s.run), after=fill(s.after), before=fill(s.before))
                for s in self.steps
            ),
            challenge=replace(
                challenge,
                ask=fill(challenge.ask),
                solution=fill(challenge.solution),
                uses=tuple(fill(name) for name in challenge.uses),
                hints=tuple(fill(h) for h in challenge.hints),
                done=fill(challenge.done),
                before=fill(challenge.before),
                target=fill(challenge.target),
            ),
            files=tuple(FileSpec(fill(f.path), fill(f.content), f.directory) for f in self.files),
            start=fill(self.start),
            vars={},
        )

    def write_files(self, home: Path) -> None:
        """練習用のファイルを home 以下に作る。"""
        home.mkdir(parents=True, exist_ok=True)
        for spec in self.files:
            path = home / spec.path
            if spec.directory:
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(spec.content)


def _content(spec: dict) -> str:
    """ファイルの中身。`lines` と `line` があれば、{n} に行番号を入れた行を lines 行ぶん並べる。"""
    if "lines" in spec:
        return "".join(spec["line"].replace("{n}", str(n)) + "\n" for n in range(1, spec["lines"] + 1))
    return spec.get("content", "")


def _load(path: Path) -> Card:
    with path.open("rb") as f:
        data = tomllib.load(f)
    command = data["command"]
    challenge = data["challenge"]
    check = challenge.get("check", "output")
    if check not in CHECKS:
        raise ValueError(f"{path.name}: check は {CHECKS} のいずれか")
    files = [FileSpec(d, directory=True) for d in data.get("dirs", [])]
    files += [FileSpec(f["path"], _content(f)) for f in data.get("files", [])]
    return Card(
        id=path.stem.split("_", 1)[-1],
        command=command,
        category=data["category"],
        title=data["title"],
        summary=data["summary"].strip(),
        when=data["when"].strip(),
        syntax=data["syntax"],
        steps=tuple(
            Step(
                s["say"].strip(),
                s["run"],
                s.get("after", "").strip(),
                s.get("before", ""),
                s.get("interactive", False),
                tuple(s.get("status", [0])),
            )
            for s in data.get("try", [])
        ),
        challenge=Challenge(
            ask=challenge["ask"].strip(),
            solution=challenge["solution"],
            check=check,
            uses=tuple(challenge.get("uses", [command])),
            hints=tuple(h.strip() for h in challenge.get("hints", [])),
            done=challenge.get("done", "").strip(),
            before=challenge.get("before", ""),
            status=tuple(challenge.get("status", [0])),
            target=challenge.get("target", ""),
        ),
        files=tuple(files),
        start=data.get("start", ""),
        prompt=data.get("prompt", "path"),
        vars={name: tuple(choices) for name, choices in data.get("vars", {}).items()},
    )


def load_cards() -> list[Card]:
    """ファイル名の順(= おすすめの順)に全カードを読み込む。"""
    return [_load(path) for path in sorted(CARDS_DIR.glob("*.toml"))]
