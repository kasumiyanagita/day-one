"""カード 1 枚分の進行: 説明 → 試し打ち → お題。

お題の正解は、カードに書かれたお手本のコマンドを、利用者と同じ環境・同じファイルで先に
実行して求める。カードを書く人は「ファイル・問い・お手本」を用意するだけでよい。
"""

from __future__ import annotations

import random
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from dayone import design, procs, sandbox
from dayone.card import Card
from dayone.judge import Snapshot, commands_in, compare_files, follows_example, snapshot, tokens
from dayone.prose import MAX_WIDTH, PANEL_CHROME, render_prose
from dayone.screen import Screen
from dayone.shell import CommandResult, Shell

HOST = "day-one"
INDENT = "  "
# 試し打ちでお手本と違うコマンドがこの回数続いたら、お手本をもう一度示す
REMIND_AFTER = 2
# お題でこの回数うまくいかなかったら、hint があることを知らせる
SUGGEST_HINT_AFTER = 3


class CardError(Exception):
    """カードの定義に問題がある(お手本のコマンドが失敗する、など)。"""


@dataclass(frozen=True)
class Reference:
    """お手本を実行した結果。"""

    output: str
    cwd: str
    before: Snapshot  # お題のお手本を実行する直前のファイルの状態
    after: Snapshot


@dataclass(frozen=True)
class Outcome:
    cleared: bool
    hints: int = 0
    misses: int = 0  # お題で正解になるまでに打った、正解でないコマンドの数(寄り道を含む)
    wrong: int = 0  # そのうち、答えようとして間違えた数
    title: bool = False  # title と打って、トップページ(一覧)に戻ることを選んだ。False で cleared でもなければ、exit で終了を選んだ

    @property
    def clean(self) -> bool:
        """ヒントなし・間違いなしで解けたか。寄り道は数えない。"""
        return self.cleared and self.hints == 0 and self.wrong == 0


def compute_reference(
    card: Card, layout: sandbox.Layout, mode: str, session: str, *, with_steps: bool = True
) -> Reference:
    """試し打ちのお手本を順に実行したうえで、お題のお手本を実行して結果を記録する。

    復習では試し打ちを飛ばすので、with_steps=False で最初の状態からお題のお手本だけを実行する。
    """
    steps = card.steps if with_steps else ()
    run = layout.run
    script = [
        'cd "$DAYONE_WORKDIR" || exit 90',
        "{",
        *(line for step in steps for line in (step.before, "" if step.interactive else step.run) if line),
        card.challenge.before or ":",
        "} > /dev/null 2>&1 < /dev/null",
        'cp -a "$HOME/." "$DAYONE_RUN/before"',
        "{",
        card.challenge.solution,
        '} > "$DAYONE_RUN/ref.out" 2> /dev/null < /dev/null',
        'echo $? > "$DAYONE_RUN/ref.status"',
        'pwd > "$DAYONE_RUN/ref.cwd"',
    ]
    (run / "ref.sh").write_text("\n".join(script) + "\n")
    (run / "before").mkdir()

    argv, env, cwd = sandbox.shell_command(
        mode,
        layout,
        host=HOST,
        workdir=card.start,
        session=session,
        command=["bash", "-c", 'bash "$DAYONE_RUN/ref.sh"'],
    )
    try:
        subprocess.run(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        status = (run / "ref.status").read_text().strip()
        if status != "0":
            raise CardError(f"{card.id}: お手本 `{card.challenge.solution}` が終了コード {status} で失敗した")
        return Reference(
            output=(run / "ref.out").read_text(errors="replace"),
            cwd=(run / "ref.cwd").read_text().strip(),
            before=snapshot(run / "before"),
            after=snapshot(layout.home),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CardError(f"{card.id}: お手本を実行できなかった: {exc}") from exc
    finally:
        # 答えを利用者のシェルから見える場所に残さない
        shutil.rmtree(run / "before", ignore_errors=True)
        for name in ("ref.sh", "ref.out", "ref.status", "ref.cwd"):
            (run / name).unlink(missing_ok=True)


class Lesson:
    def __init__(
        self,
        card: Card,
        console: Console,
        mode: str,
        rng: random.Random | None = None,
        *,
        review: bool = False,
        screen: Screen | None = None,
        input_fd: int | None = None,
        write=None,
        on_idle=None,
    ):
        self.card = card.instantiate(rng or random.Random())
        self.console = console
        # デザインの色の名前(d.〜)を、この Console で使えるようにする(呼び出し側が渡し忘れていても描ける)
        console.push_theme(design.theme())
        self.mode = mode
        # 復習では説明と試し打ちを飛ばして、お題だけを出す
        self.review = review
        self.screen = screen
        self._input_fd = input_fd
        self._write = write
        self._on_idle = on_idle
        self.session = secrets.token_hex(8)

        self.shell: Shell | None = None
        self.home: Path | None = None
        self.reference: Reference | None = None
        self._step = len(self.card.steps) if review else 0  # 試し打ちの何手目か。手順を終えるとお題に入る
        self._step_misses = 0
        self._hints = 0
        self._misses = 0
        self._wrong = 0
        self._used: set[str] = set()  # お題に入ってから使ったコマンド

        # 入力欄の下に出したままにする内容(画面を分けているときだけ使う)
        self._task: tuple[Text, str] = (Text(), "")  # 見出しと、いま取り組むことの文章
        self._pinned_hints: list[tuple[str, str]] = []  # (印, ヒントの文章)
        self._feedback: tuple[str, str] | None = None  # 直前のコマンドへの指摘と、その色
        self._fresh = False  # 段階が進んだ直後(次のコマンドを打つまで、見出しを目立たせる)
        self._to_title = False  # title と打って、カードの途中でトップページに戻ることを選んだ

    # ---- 進行 ---------------------------------------------------------

    def play(self) -> Outcome:
        layout = sandbox.Layout(Path(tempfile.mkdtemp(prefix="dayone-")))
        try:
            layout.create()
            self.home = layout.home
            pristine = layout.base / "pristine"
            self.card.write_files(pristine)

            # お手本を実行して正解を求めたあと、ファイルを最初の状態に戻して利用者に渡す
            shutil.copytree(pristine, layout.home, dirs_exist_ok=True)
            self.reference = compute_reference(
                self.card, layout, self.mode, self.session, with_steps=not self.review
            )
            shutil.rmtree(layout.home)
            shutil.copytree(pristine, layout.home)

            (layout.run / "bashrc").write_text((sandbox.SHELL_DIR / "bashrc").read_text())
            shutil.copy(sandbox.SHELL_DIR / "guard.bash", layout.run / "guard.bash")
            argv, env, cwd = sandbox.shell_command(
                self.mode,
                layout,
                host=HOST,
                workdir=self.card.start,
                session=self.session,
                extra={"DAYONE_PROMPT": self.card.prompt},
            )
            self._show_intro()
            self.shell = Shell(argv, env, cwd, input_fd=self._input_fd, write=self._write, screen=self.screen)
            self.shell.on_idle = self._on_idle
            try:
                cleared = self.shell.run(self._on_command, on_ready=self._enter_stage)
            finally:
                self.shell.close()
            if self._to_title:
                return Outcome(False, title=True)
            return Outcome(cleared, self._hints, self._misses, self._wrong)
        finally:
            if self.screen is not None:
                self.screen.set_task(None)
            procs.kill_session(self.session)
            layout.remove()

    @property
    def _in_challenge(self) -> bool:
        return self._step >= len(self.card.steps)

    def _enter_stage(self) -> None:
        """いまの段階(試し打ちの 1 手、またはお題)の案内を出す。"""
        total = len(self.card.steps)
        if self._in_challenge:
            challenge = self.card.challenge
            if challenge.before:
                self.shell.run_hidden(challenge.before)
            self._show_task(f"[d.heading]{design.active.HEADING_MARK} お題[/]", challenge.ask)
            return
        step = self.card.steps[self._step]
        if step.before:
            self.shell.run_hidden(step.before)
        heading = f"[d.heading]{design.active.HEADING_MARK} 打ってみよう[/] [d.muted]{self._step + 1}/{total}[/]"
        self._show_task(heading, step.say)

    def _track(self) -> str:
        """カードの中の進み具合。試し打ちの 1 手を ●、お題を ★ で表す。復習はお題だけなので出さない。"""
        if self.review:
            return ""
        total = len(self.card.steps)
        done = min(self._step, total)
        look = design.active
        if self._in_challenge:
            return f"[d.done]{look.STEP_DONE * total}[/][d.now]{look.GOAL_NOW}[/]"
        # 済んだ手は緑、いまの手は白の太字、これからの手は薄い色
        upcoming = look.STEP_TODO * (total - done - 1) + look.GOAL_TODO
        return f"[d.done]{look.STEP_DONE * done}[/][d.now]{look.STEP_DONE}[/][d.muted]{upcoming}[/]"

    def _show_task(self, heading: str, text: str) -> None:
        """いま取り組むことを知らせる。画面を分けているときは、入力欄の下に出したままにする。"""
        if self.screen is not None:
            self._task = (Text.from_markup(heading), text)
            self._pinned_hints = []
            self._feedback = None
            self._refresh_task()
            return
        self.console.print(f"\n{heading}")
        self._say(text)
        if self._in_challenge:
            self.console.print(f"{INDENT}[d.muted]hint でヒント / title でトップページに戻る / exit で終了[/d.muted]")
        self.console.print()

    def _refresh_task(self) -> None:
        """入力欄の下の内容(お題、開いたヒント、直前の指摘)を描き直す。"""
        if self.screen is not None:
            self.screen.set_task(self._task_text)

    def _task_text(self, width: int) -> Text:
        width = min(width, MAX_WIDTH)
        indent = f" {INDENT}"
        title, text = self._task
        title = title.copy()
        look = design.active
        # 見出しの右に添えるもの(進み具合の印と、段階が進んだときの一言)。出すかどうかはデザインが決める
        extras = [self._track()] if look.SHOW_TRACK else []
        if self._fresh:
            # 段階が進んだことに気づけるよう、次のコマンドを打つまで見出しを目立たせる
            title.stylize("d.fresh")
            if look.ADVANCED_NOTE:
                extras.append(f"[d.success]{look.ADVANCED_NOTE}[/]")
        heading = Text.assemble(" ", title, "  ", Text.from_markup("  ".join(part for part in extras if part)))
        heading.rstrip()
        lines = [heading, self._prose(text, width, indent=indent)]
        lines += [self._prose(hint, width, mark=mark, indent=indent) for mark, hint in self._pinned_hints]
        if self._feedback:
            message, style = self._feedback
            lines.append(self._prose(message, width, mark=f"{design.active.NOTE_MARK} ", style=style, indent=indent))
        return Text("\n").join(lines)

    def _note(self, message: str, style: str = "d.hint") -> None:
        """直前のコマンドへの指摘。画面を分けているときは、次のコマンドを打つまでお題の下に出しておく。"""
        if self.screen is None:
            self._say(message, style=style)
            return
        self._feedback = (message, style)

    def _on_command(self, result: CommandResult) -> bool:
        """利用者が 1 行打つたびに呼ばれる。カードを終えるときに True を返す。"""
        # 出力が改行で終わっていないときは、案内が同じ行に続かないようにする
        if result.output and not result.output.endswith("\n"):
            self.console.print()

        self._feedback = None
        self._fresh = False
        step = self._step
        cleared = self._respond(result)
        if not cleared:
            self._fresh = self._step != step
            self._refresh_task()
        return cleared

    def _respond(self, result: CommandResult) -> bool:
        if result.command == "hint":
            self._hint()
            return False
        if result.command == "card":
            self._show_intro(full=True)
            return False
        if result.command == "title":
            self._to_title = True
            return True
        if self._in_challenge:
            return self._on_challenge(result)
        self._on_step(result)
        return False

    def _on_step(self, result: CommandResult) -> None:
        step = self.card.steps[self._step]
        if result.status == 0 and follows_example(result.command, step.run):
            if step.after:
                self.console.print()
                self._say(step.after, mark=f"[d.done]{design.active.CHECK}[/] ")
            self._step += 1
            self._step_misses = 0
            self._enter_stage()
            return
        self._step_misses += 1
        if self._step_misses >= REMIND_AFTER:
            self._step_misses = 0
            self._note(f"自由に試して大丈夫です。先へ進むには `{step.run}` と打ってください。", style="d.muted")

    def _on_challenge(self, result: CommandResult) -> bool:
        challenge = self.card.challenge
        used = commands_in(result.command)
        self._used |= used

        problem = ""
        if challenge.check == "command":
            # less や man のように画面を使うコマンドは、出力ではなく「開いて、正常に閉じたか」を見る
            result_ok = uses_ok = result.status == 0 and follows_example(result.command, challenge.solution)
            problem = "開くものが、お題と違うようです。"
        elif challenge.check == "output":
            result_ok = result.status == 0 and tokens(result.output) == tokens(self.reference.output)
            uses_ok = all(name in used for name in challenge.uses)
            problem = "お題の答えとは、出力が違うようです。"
        else:
            # 状態で判定するお題は、何手かに分けて達成してもよい
            uses_ok = all(name in self._used for name in challenge.uses)
            if challenge.check == "cwd":
                result_ok = result.cwd == self.reference.cwd
                problem = "いまいる場所が、お題と違うようです。"
            else:
                problem = compare_files(snapshot(self.home), self.reference.before, self.reference.after) or ""
                result_ok = not problem

        names = " と ".join(f"`{name}`" for name in challenge.uses)
        if result_ok and uses_ok:
            learned = "" if self.review else f"  [d.command]{self.card.command}[/] [d.done]をおぼえた[/d.done]"
            self.console.print(f"\n[d.success]正解![/d.success]{learned}")
            if challenge.done:
                self.console.print()
                self._say(challenge.done)
            return True

        self._misses += 1
        if challenge.check == "cwd" and self.reference.cwd.startswith(result.cwd.rstrip("/") + "/"):
            # 目的地へ向かう途中のディレクトリにいる。1 つずつ移動している最中なので、間違いではない
            return False
        if result_ok or all(name in used for name in challenge.uses):
            self._wrong += 1
        if result_ok:
            self._note(f"結果は合っています。ここでは {names} を使って出してみましょう。")
        elif all(name in used for name in challenge.uses):
            self._note(f"{challenge.uses[0]} は使えています。{problem}")
        elif self._misses % SUGGEST_HINT_AFTER == 0:
            self._note("行き詰まったら `hint` と打つとヒントが出ます。", style="d.muted")
        return False

    def _hint(self) -> None:
        """ヒントを 1 つ開く。画面を分けているときは、開いたヒントをお題の下に出したままにする。"""
        if not self._in_challenge:
            step = self.card.steps[self._step]
            opened = [("[d.hint]ヒント[/d.hint] ", f"`{step.run}` と打ってみましょう。")]
        else:
            hints = self.card.challenge.hints
            if self._hints < len(hints):
                self._hints += 1
            opened = [
                (f"[d.hint]ヒント {number}/{len(hints)}[/d.hint] ", hint)
                for number, hint in enumerate(hints[: self._hints], start=1)
            ]
            if not hints:
                self._note("このお題にヒントはありません。", style="d.muted")
        if self.screen is not None:
            self._pinned_hints = opened
            return
        for mark, hint in opened:
            self._say(hint, mark=mark)

    # ---- 表示 ---------------------------------------------------------

    @property
    def _width(self) -> int:
        return min(self.console.width, MAX_WIDTH)

    def _say(self, text: str, *, mark: str = "", style: str = "") -> None:
        """案内の文章を、表示の部分に出す。"""
        self.console.print(self._prose(text, self._width, mark=mark, style=style))

    @staticmethod
    def _prose(text: str, width: int, *, mark: str = "", style: str = "", indent: str = INDENT) -> Text:
        """案内の文章を、字下げして幅 width に折り返す。mark は 1 行目の先頭に付ける印。"""
        mark_width = Text.from_markup(mark).cell_len
        body = render_prose(text, width - mark_width, indent=indent)
        if style:
            body.stylize(style)
        if not mark:
            return body
        # 印の幅だけ 2 行目以降を下げて、文章の左端をそろえる
        lines = body.split("\n")
        first = Text.assemble(indent, Text.from_markup(mark), lines[0][len(indent) :])
        rest = [Text.assemble(" " * mark_width, line) for line in lines[1:]]
        return Text("\n").join([first, *rest])

    def _show_status(self) -> None:
        """入力欄の上の線に、左端にはどのカードにいるか、右端には使える操作を出す。"""
        if self.screen is None:
            return
        self.screen.set_status(
            "[d.key]hint[/d.key] ヒント",
            "[d.key]card[/d.key] 説明",
            "[d.key]title[/d.key] トップページに戻る",
            "[d.key]exit[/d.key] 終了",
            title=f"[d.command]{self.card.command}[/]" + (f" [d.review]復習[/]" if self.review else ""),
        )

    def _show_intro(self, full: bool = False) -> None:
        """カードの説明を表示する。復習では見出しだけにする(full なら復習でも説明を出す)。"""
        self._show_status()
        card = self.card
        if self.review and not full:
            self.console.print(
                f"\n[d.review]復習[/]  [d.command]{card.command}[/] [d.muted]{design.active.TITLE_DASH}[/] {card.title}"
            )
            return
        inner = self._width - PANEL_CHROME - 2
        label_width = 10
        # 3 つとも「見出し + 内容」の同じ形で並べ、あいだを 1 行ずつ空ける
        rows = [
            self._labelled("説明", card.summary, inner, label_width),
            Text(),
            self._labelled("いつ使う", card.when, inner, label_width),
            Text(),
            self._labelled("syntax", f"`{card.syntax}`", inner, label_width),
        ]
        self.console.print()
        self.console.print(
            Panel(
                Group(*rows),
                # 枠の線は薄く、見出しは「打つもの(水色)─ 説明(白の太字)」にして、線より中身を目立たせる
                title=f"[d.frame.command]{card.command}[/] {design.active.TITLE_DASH} [d.frame.caption]{card.title}[/]",
                box=design.active.BOX,
                title_align="left",
                border_style="d.frame",
                padding=(1, 2),
                width=self._width,
            )
        )

    @staticmethod
    def _labelled(label: str, text: str, width: int, label_width: int) -> Text:
        body = render_prose(text, width, indent=" " * label_width)
        lines = body.split("\n")
        pad = " " * max(1, label_width - Text(label).cell_len)
        first = Text.assemble((label, "dim"), pad, lines[0][label_width:])
        return Text("\n").join([first, *lines[1:]])
