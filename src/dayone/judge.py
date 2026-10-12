"""お題の判定に使う部品: 出力の比較、使ったコマンドの抽出、ファイルの状態の比較。"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

# 端末の制御文字列(色、カーソル移動、タイトル設定など)
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
# 1 行の中で新しいコマンドが始まる区切り
_COMMAND_BOUNDARY = re.compile(r"\|\||&&|\$\(|[|;`(\n]")
_ASSIGNMENT = re.compile(r"^\w+=")
_COMMAND_NAME = re.compile(r"[\w.+-]+")
_OPERATOR = re.compile(r"(>>|>|\|)")
_SHORT_FLAGS = re.compile(r"-\w+")

Snapshot = dict[str, tuple[str, str]]  # 相対パス → (種類, 中身の要約)


def plain(output: str) -> str:
    """端末に出た内容から制御文字列を取り除く。"""
    return _ANSI.sub("", output).replace("\r", "")


def tokens(output: str) -> list[str]:
    """空白で区切った語の並び。

    端末では `ls` が複数列になり、パイプ越しでは 1 列になる、といった見た目の違いを吸収して比べる。
    """
    return plain(output).split()


def commands_in(line: str) -> set[str]:
    """1 行の中で実行されるコマンド名。パイプや `;` で区切った各部分の先頭の語を見る。"""
    found = set()
    for part in _COMMAND_BOUNDARY.split(line):
        words = [word for word in part.split() if not _ASSIGNMENT.match(word)]
        # `$(pwd)"` のように閉じ括弧や引用符が続いていても、名前の部分だけを取る
        if words and (name := _COMMAND_NAME.match(words[0].rsplit("/", 1)[-1])):
            found.add(name.group())
    return found


def _split_example(line: str) -> tuple[list[str], set[str]]:
    """1 行を (オプション以外の語の並び, 短いオプションの文字の集まり) に分ける。"""
    # `>file` や `a|b` のように空白なしで書かれた記号も、1 つの語として扱う
    words = _OPERATOR.sub(r" \1 ", line).split()
    flags = {letter for word in words if _SHORT_FLAGS.fullmatch(word) for letter in word[1:]}
    return sorted(word for word in words if not _SHORT_FLAGS.fullmatch(word)), flags


def follows_example(typed: str, example: str) -> bool:
    """typed が、お手本 example と同じコマンド・引数で、お手本のオプションを含んでいるか。

    語の順序と、オプションの追加は問わない。`-l` と `-a` をまとめた `-la` のような書き方も認める。
    引数が増えたり違ったりしている場合は、別の操作とみなす。
    """
    typed_words, typed_flags = _split_example(typed)
    example_words, example_flags = _split_example(example)
    return typed_words == example_words and example_flags <= typed_flags


def snapshot(root: Path) -> Snapshot:
    """root 以下のファイルとディレクトリの一覧と中身の要約。"""
    result: Snapshot = {}
    for directory, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(directory, name)
            rel = str(path.relative_to(root))
            if path.is_symlink():
                result[rel] = ("link", os.readlink(path))
            elif path.is_dir():
                result[rel] = ("dir", "")
            else:
                try:
                    result[rel] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
                except OSError:
                    result[rel] = ("file", "unreadable")
    return result


def compare_files(actual: Snapshot, before: Snapshot, after: Snapshot) -> str | None:
    """お手本の操作(before → after)と同じ変化が actual に起きているかを調べる。

    問題なければ None、違っていれば利用者に見せる一言を返す。
    お手本が触らなかったファイルは「残っていること」だけを確かめ、余分なファイルは問わない。
    """
    # やり直しが必要になる問題(残すはずのものを消した)を、いちばん先に知らせる
    for rel in sorted(after):
        if before.get(rel) == after[rel] and rel not in actual:
            return (
                f"`{rel}` がなくなっています。これは残しておくものでした。"
                "`title` でトップページに戻って開き直すと、最初の状態からやり直せます。"
            )

    for rel in sorted(set(before) - set(after)):
        if rel in actual:
            return f"`{rel}` がまだ残っています。"

    for rel, (kind, digest) in sorted(after.items()):
        changed = before.get(rel) != (kind, digest)
        if rel not in actual:
            return f"`{rel}` がまだありません。"
        actual_kind, actual_digest = actual[rel]
        if actual_kind != kind:
            expected = {"dir": "ディレクトリ", "file": "ファイル", "link": "リンク"}[kind]
            return f"`{rel}` は{expected}のはずです。"
        if changed and actual_digest != digest:
            return f"`{rel}` の中身がお題と違います。"
    return None
