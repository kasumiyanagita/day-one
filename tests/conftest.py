"""カードを実際のシェルで動かすためのテスト用ヘルパー。"""

import io
import os
import random
import threading
from dataclasses import dataclass

import pytest
from rich.console import Console

from dayone import sandbox
from dayone.card import load_cards
from dayone.judge import commands_in
from dayone.lesson import Lesson, Outcome

CARDS = {card.id: card for card in load_cards()}
# 画面を使うコマンドと、それを閉じるキー
QUIT_KEYS = {"less": "q", "man": "q", "vim": ":q!\r"}
MODES = [sandbox.GUARD] + ([sandbox.ISOLATED] if sandbox.isolation_available() else [])


@pytest.fixture(params=MODES)
def mode(request):
    return request.param


@dataclass
class Played:
    outcome: Outcome
    screen: str  # アプリが出した案内
    terminal: str  # シェルから中継された出力
    lesson: Lesson


def play(card, lines, mode, seed=0, review=False) -> Played:
    """カードを開き、lines を 1 行ずつ打ち込む。打ち終えたら入力を閉じてカードを抜ける。

    lines の要素は文字列か、今回のカード(変数を埋めたもの)を受け取って文字列を返す関数。
    """
    read_fd, write_fd = os.pipe()
    queue = list(lines)
    screen, terminal = io.StringIO(), bytearray()
    console = Console(file=screen, width=100, force_terminal=False, color_system=None)

    def type_next() -> None:
        nonlocal write_fd
        if write_fd is None:
            return
        if not queue:
            os.close(write_fd)
            write_fd = None
            return
        line = queue.pop(0)
        if callable(line):
            line = line(lesson.card)
        os.write(write_fd, f"{line}\n".encode())
        keys = [QUIT_KEYS[name] for name in commands_in(line) if name in QUIT_KEYS]
        if keys and " -es " not in line:  # vim -es は画面を使わずに終わる
            # 画面を使うコマンドは閉じるまで終わらないので、少し待ってから閉じる
            threading.Timer(0.7, quit_screen, keys[:1]).start()

    def quit_screen(keys: str) -> None:
        if write_fd is not None:
            os.write(write_fd, keys.encode())

    lesson = Lesson(
        card,
        console,
        mode,
        random.Random(seed),
        review=review,
        input_fd=read_fd,
        write=terminal.extend,
        on_idle=type_next,
    )
    try:
        outcome = lesson.play()
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
    return Played(outcome, screen.getvalue(), terminal.decode(errors="replace"), lesson)


def walkthrough(card):
    """お手本どおりに進める入力: 試し打ちをすべて打ち、お題のお手本を打つ。"""
    return [*(lambda c, i=i: c.steps[i].run for i in range(len(card.steps))), lambda c: c.challenge.solution]
