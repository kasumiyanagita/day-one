"""アプリ全体を擬似端末で起動し、一覧からカードを開いて終了するまでを通す。"""

import fcntl
import os
import pty
import select
import struct
import sys
import termios
import time

import pytest


def run_app(tmp_path, steps, rows=24, columns=80, first_time=False):
    """dayone を起動し、steps のキー入力を順に送る。(画面に出た内容, 終了コード) を返す。

    first_time でなければ、チュートリアルを見終えた状態(トップページから始まる)にしておく。
    """
    save = tmp_path / "progress.json"
    if not first_time and not save.exists():
        save.write_text('{"version": 1, "tutorial_seen": true, "cards": {}}')
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(DAYONE_DATA_DIR=str(tmp_path), TERM="xterm-256color")
        os.execv(sys.executable, [sys.executable, "-m", "dayone", "--seed", "1"])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
    output = bytearray()

    def read_until_quiet(quiet=0.8, limit=15.0):
        """出力が quiet 秒とぎれるまで読む(マシンが重くても、アプリの準備が終わるのを待てる)。"""
        deadline = time.time() + limit
        last = time.time()
        while time.time() < deadline and time.time() - last < quiet:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    return
                if not data:
                    return
                output.extend(data)
                last = time.time()

    read_until_quiet(quiet=1.5)
    for keys in steps:
        os.write(fd, keys.encode())
        read_until_quiet()
    # 最後の入力で終了するはずなので、終わるまで少し待つ
    deadline = time.time() + 10
    finished, status = os.waitpid(pid, os.WNOHANG)
    while not finished and time.time() < deadline:
        time.sleep(0.1)
        finished, status = os.waitpid(pid, os.WNOHANG)
    if not finished:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        pytest.fail("アプリが終了しなかった:\n" + output.decode(errors="replace")[-2000:])
    return output.decode(errors="replace"), os.waitstatus_to_exitcode(status)


@pytest.mark.parametrize("choice", ["pwd", "vim-edit", "\r"])
def test_open_a_card_and_go_back_to_the_top_page(tmp_path, choice):
    screen, code = run_app(tmp_path, [choice if choice == "\r" else choice + "\r", "title\r", "exit\r"])
    assert "Traceback" not in screen
    assert screen.count("で実行中") == 2  # トップページが 2 回出ている(起動時と、カードの途中の title で戻ったとき)
    assert "おつかれさまでした" in screen and code == 0


def test_exit_in_a_card_quits_the_app(tmp_path):
    screen, code = run_app(tmp_path, ["ls\r", "exit\r"])
    assert "Traceback" not in screen
    assert screen.count("で実行中") == 1  # トップページには戻らない
    assert "おつかれさまでした" in screen and code == 0


def test_only_exit_quits_from_the_top_page(tmp_path):
    screen, code = run_app(tmp_path, ["zzz\r", "1\r", "\x1b[C", "\x1b[D", "m\r", "q\r", "top\r", "title\r", "exit\r"])
    assert "Traceback" not in screen
    assert screen.count("入力してください") == 6  # トップページでは m も q も top も title も働かない
    assert "ページ 2/4" in screen
    assert "おつかれさまでした" in screen and code == 0


def test_title_and_exit_after_clearing_a_card(tmp_path):
    # pwd のカードを最後まで解き、「次へ」の画面で title と打ってトップページに戻る
    screen, code = run_app(tmp_path, ["pwd\r", "pwd\r", "pwd\r", "title\r", "exit\r"])
    assert "Traceback" not in screen
    assert "正解" in screen and "トップページに戻る" in screen
    assert screen.count("で実行中") == 2
    assert "おつかれさまでした" in screen and code == 0

    # 「次へ」の画面の exit は、そのまま終了する
    screen, code = run_app(tmp_path, ["pwd\r", "pwd\r", "pwd\r", "exit\r"])
    assert screen.count("で実行中") == 1 and "おつかれさまでした" in screen and code == 0


def test_falling_back_to_guard_mode_asks_first(tmp_path, monkeypatch):
    """隔離できない環境では、簡易モードで始める前に確かめる。n なら始めない。"""
    from rich.console import Console

    from dayone import cli, sandbox

    monkeypatch.setattr(sandbox, "resolve_mode", lambda requested: sandbox.GUARD)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("DAYONE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(Console, "input", lambda self, prompt="": "n")
    assert cli.main([]) == 0  # 一覧を出さずに終わる(出していれば、端末の設定に失敗して例外になる)


def test_plain_dayone_never_opens_a_retro_tab(tmp_path, monkeypatch):
    """dayone だけなら通常のデザインで、いまの端末で動く。タブを開くのは --retro のときだけ。"""
    from dayone import cli, retro

    opened = []
    monkeypatch.setattr(retro, "can_open_tab", lambda: True)
    monkeypatch.setattr(retro, "open_tab", lambda arguments: opened.append(arguments) or True)
    monkeypatch.delenv("DAYONE_RETRO", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)  # 端末がないので、タブを開かなければここで終わる
    assert cli.main([]) == 2 and opened == []
    assert cli.main(["--retro", "--seed", "3"]) == 0 and opened == [["--retro", "--seed", "3"]]
    # レトロなタブの中では、もう一度タブを開かない
    monkeypatch.setenv("DAYONE_RETRO", "1")
    assert cli.main(["--retro"]) == 2 and len(opened) == 1


def test_tutorial_shows_on_first_launch_only_and_can_be_reopened(tmp_path):
    # 初めての起動: チュートリアルが出る。Enter で最後まで進むと、トップページになる
    screen, code = run_app(tmp_path, ["\r", "\r", "\r", "\r", "exit\r"], first_time=True)
    assert "Traceback" not in screen
    assert "チュートリアル 1/4" in screen and "チュートリアル 4/4" in screen
    assert screen.index("チュートリアル 4/4") < screen.index("コマンド一覧")
    assert "おつかれさまでした" in screen and code == 0

    # 2 回目の起動: チュートリアルは出ず、トップページから始まる。tutorial と打てば、また見られる
    screen, code = run_app(tmp_path, ["tutorial\r", "\x1b[C", "\x1b[D", "title\r", "exit\r"])
    assert screen.index("コマンド一覧") < screen.index("チュートリアル 1/4") < screen.index("チュートリアル 2/4")
    assert screen.count("コマンド一覧") >= 2 and code == 0


def test_exit_in_the_tutorial_quits_and_does_not_show_it_again(tmp_path):
    screen, code = run_app(tmp_path, ["exit\r"], first_time=True)
    assert "チュートリアル 1/4" in screen and "コマンド一覧" not in screen and code == 0
    screen, code = run_app(tmp_path, ["exit\r"])
    assert "チュートリアル" not in screen.replace("tutorial", "") or "チュートリアル 1/4" not in screen
    assert "コマンド一覧" in screen and code == 0
