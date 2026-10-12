"""エントリポイントとメニュー。"""

from __future__ import annotations

import argparse
import itertools
import random
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from rich.cells import cell_len
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from dayone import __version__, design, retro, sandbox, tutorial
from dayone.card import Card, load_cards
from dayone.lesson import Lesson
from dayone.links import load_links
from dayone.progress import MAX_LEVEL, Progress, Recorded, default_path, today
from dayone.screen import RESIZED, Screen

MODE_LABELS = {
    sandbox.ISOLATED: "[d.mode.isolated]隔離モード[/]で実行中",
    sandbox.GUARD: "[d.mode.guard]簡易モード[/]で実行中",
}

QUIT = "quit"
MENU = "menu"
NEXT_PAGE = "next-page"
PREVIOUS_PAGE = "previous-page"
TUTORIAL = "tutorial"
REDRAW = "redraw"  # 端末の大きさが変わったので、一覧を新しい大きさで描き直す

FRAME_ROWS = 2  # 一覧を囲む枠の、上下の線
FRAME_COLUMNS = 4  # 一覧を囲む枠の、左右の線と余白
# 枠の内側の幅がこれ以上なら、一覧を 2 列に並べる(端末の幅でいえば 80 桁以上)
TWO_COLUMNS_FROM = 80 - FRAME_COLUMNS
COLUMN_GAP = 2
MIN_MENU_ROWS = 4  # 端末が小さくても、1 ページにこの行数は並べる
MENU_SPARE_ROWS = 2
MENU_PLACEHOLDER = "Enter / コマンドを入力(例: cd)"
GAUGE_WIDTH = 20  # 一覧の下に出す進捗バーの長さ(桁数)
# 一覧で何も打っていないときの矢印キー。→ で次、← で前のページへ移る
PAGE_KEYS = {"\x1b[C": NEXT_PAGE, "\x1bOC": NEXT_PAGE, "\x1b[D": PREVIOUS_PAGE, "\x1bOD": PREVIOUS_PAGE}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dayone", description="コマンドを 1 つずつ、実際に打って覚えるターミナルアプリ"
    )
    parser.add_argument("--version", action="version", version=f"dayone {__version__}")
    parser.add_argument(
        "--mode",
        choices=[sandbox.AUTO, sandbox.ISOLATED, sandbox.GUARD],
        default=sandbox.AUTO,
        help="シェルの実行方式 (既定: auto = 使えれば隔離モード)",
    )
    parser.add_argument("--seed", type=int, help="お題の選び方を固定する (動作確認用)")
    parser.add_argument("--reset-progress", action="store_true", help="セーブデータを消して最初から始める")
    parser.add_argument("--retro", action="store_true", help="レトロなデザインで動かす (専用のタブを開く)")
    parser.add_argument(
        "--design",
        choices=sorted(design.DESIGNS),
        help="タブを開かず、いまの端末のまま、指定したデザインで描く (動作確認用)",
    )
    parser.add_argument(
        "--retro-install", action="store_true", help="Windows Terminal に、レトロな見た目のプロファイルを追加する"
    )
    parser.add_argument("--retro-remove", action="store_true", help="追加したレトロな見た目のプロファイルを取り除く")
    return parser.parse_args(argv)


def _dots(level: int) -> str:
    return design.active.LEVEL_ON * (level + 1) + design.active.LEVEL_OFF * (MAX_LEVEL - level)


def _in_days(days: int) -> str:
    """「次の復習は◯◯」の◯◯に入る言い方。"""
    return "明日" if days == 1 else f" {days} 日後"


def _mark(progress: Progress, card: Card, on: date) -> str:
    """一覧でカードの左に付ける印。絵文字として表示されることのない文字を使う。"""
    record = progress.cards.get(card.id)
    if record is None:
        return " "
    return f"[d.review]{design.active.DUE}[/]" if record.is_due(on) else f"[d.done]{design.active.CHECK}[/]"


def _oncall_mark(card: Card) -> str:
    """oncall を始める前に覚えておきたいコマンドに付ける印。それ以外は、同じ幅の空白。"""
    return f"[d.oncall]{design.active.ONCALL}[/]" if card.id in load_links().ready else " "


def _menu_blocks(cards: list[Card], progress: Progress, on: date) -> list[list[Text]]:
    """分類ごとの行のまとまり。見出しが 1 行、そのあとにカード 1 枚につき 1 行。"""
    command_width = max(cell_len(card.command) for card in cards)
    blocks = []
    for category in dict.fromkeys(card.category for card in cards):
        members = [card for card in cards if card.category == category]
        learned = sum(1 for card in members if progress.is_learned(card.id))
        bar = f"[d.done]{design.active.BAR_ON * learned}[/][d.muted]{design.active.BAR_OFF * (len(members) - learned)}[/]"
        block = [Text.from_markup(f"  [d.heading]{category}[/]  {bar}  [d.muted]{learned}/{len(members)}[/d.muted]")]
        for card in members:
            pad = " " * (command_width - cell_len(card.command))
            block.append(
                Text.from_markup(
                    # 番号は何枚目かの目安として出す(番号では選べない。q と 1 の押し間違いを避けるため)
                    f"  [d.muted]{cards.index(card) + 1:>3}[/d.muted] {_mark(progress, card, on)} {_oncall_mark(card)} "
                    f"[d.command]{card.command}[/]{pad}  {card.title}"
                )
            )
        blocks.append(block)
    return blocks


def _flow(blocks: list[list[Text]], height_of: Callable[[int], int]) -> list[list[Text]]:
    """行のまとまりを、上から順に列へ流し込む。height_of(列の番号) が、その列に入る行数。"""
    columns: list[list[Text]] = [[]]

    def room() -> int:
        return height_of(len(columns) - 1) - len(columns[-1])

    for block in blocks:
        if columns[-1]:
            # まとまり全体が次の列に収まるなら、途中で分けずに次の列へ送る。
            # 収まらないほど長くても、見出しだけが列の最後に残らないようにする
            needed = len(block) if len(block) <= height_of(len(columns)) else 2
            if room() < 1 + needed:
                columns.append([])
            else:
                columns[-1].append(Text())
        for position, line in enumerate(block):
            if room() <= 0:
                columns.append([])
                if position:
                    # 分類の途中で列が変わったら、どの分類の続きかが分かるよう見出しを付け直す
                    columns[-1].append(Text.assemble(block[0], (" (つづき)", "dim")))
            columns[-1].append(line)
    return columns


def _menu_pages(cards: list[Card], progress: Progress, on: date, width: int, height: int) -> list[list[Text]]:
    """一覧を、高さ height 行に収まるページに分ける。各ページは、表示する行の並び。幅があれば 2 列に並べる。"""
    blocks = _menu_blocks(cards, progress, on)
    per_page = 2 if width >= TWO_COLUMNS_FROM else 1
    natural = max(line.cell_len for block in blocks for line in block)
    column_width = min((width - COLUMN_GAP * (per_page - 1)) // per_page, natural)
    columns = _flow(blocks, lambda index: height)
    # 1 ページに収まるなら、左の列だけが長くならないよう、収まる範囲でいちばん低い高さにそろえる
    for lower in range(1, height):
        if len(balanced := _flow(blocks, lambda index: lower)) <= per_page:
            columns = balanced
            break

    pages = []
    for start in range(0, len(columns), per_page):
        rows = []
        for cells in itertools.zip_longest(*columns[start : start + per_page], fillvalue=Text()):
            row = Text()
            for position, cell in enumerate(cells):
                last = position == len(cells) - 1
                cell = cell.copy()
                cell.truncate(column_width, overflow="ellipsis", pad=not last)
                row.append_text(cell)
                if not last:
                    row.append(" " * COLUMN_GAP)
            rows.append(row)
        pages.append(rows)
    # どのページも同じ行数にそろえる(ページを送っても、下に出す集計の位置が動かないようにする)
    tallest = max(len(rows) for rows in pages)
    return [rows + [Text()] * (tallest - len(rows)) for rows in pages]


def _line_count(console: Console, lines: list[str]) -> int:
    """lines を表示したときに使う行数(幅が狭くて折り返す分を含む)。"""
    with console.capture() as capture:
        for line in lines:
            console.print(line, highlight=False)
    return capture.get().count("\n")


def _show_menu(
    console: Console, rows: int, cards: list[Card], progress: Progress, mode: str, on: date, page: int
) -> tuple[int, int]:
    """分類ごとにカードを並べた地図を、rows 行に収まる分だけ表示する。(表示したページ, ページ数) を返す。"""
    # ロゴと、その下の行(ひとことの説明、実行方式)は、どのページにも出す。下の行を出すかどうかはデザインが決める。
    # 行数に余裕があれば、ロゴの下と説明の下を 2 行ずつ空ける。余裕がなければ空けない(すぐ下に、一覧を囲む枠の線が来る)
    def header_lines(roomy: bool) -> list[str]:
        look = design.active
        lines = [f"[d.logo]{look.LOGO.strip(chr(10))}[/]"]
        below = ([f"  {look.TAGLINE}"] if look.TAGLINE else []) + ([f"  {MODE_LABELS[mode]}"] if look.SHOW_MODE else [])
        if not below:
            return [lines[0] + ("\n" if roomy else "")]
        lines[0] += "\n\n" if roomy else ""
        below[-1] += "\n\n" if roomy else ""
        return lines + below

    # 進み具合の 2 行は、見出しの幅をそろえて同じ形で出し、見出しに進捗度(%)を添える
    labels = ["おぼえたコマンド", "oncall への準備"]
    label_width = max(cell_len(label) for label in labels)

    def gauge(label: str, done: int, total: int, lead: str = " ") -> str:
        """進み具合の 1 行。lead は行の先頭に置く印(なければ空白)。"""
        pad = " " * (label_width - cell_len(label))
        # バーは枚数ではなく割合で塗る(カードが増えても長さが変わらない)。
        # 1 枚でも覚えていれば 1 マスは塗り、全部覚えるまでは満杯にしない
        filled = done * GAUGE_WIDTH // total
        filled = min(max(filled, 1 if done else 0), GAUGE_WIDTH - (done < total))
        # 分類ごとの ■□ と違い、ここは切れ目のない 1 本の線で出す(覚えた分が緑、残りが薄い色)
        bar = f"[d.done]{design.active.GAUGE_ON * filled}[/][d.muted]{design.active.GAUGE_OFF * (GAUGE_WIDTH - filled)}[/]"
        return f"{lead} [d.muted]{label}{pad} {done * 100 // total:>3}%[/d.muted]  {bar}  [d.muted]{done}/{total}[/d.muted]"

    learned_total = sum(1 for card in cards if progress.is_learned(card.id))
    due = len(progress.due([card.id for card in cards], on))
    review = f"   [d.review]今日の復習 {due} 個[/]" if due else ""
    footer = [gauge(labels[0], learned_total, len(cards)) + review]
    # 次の一歩である oncall に向けて、土台になるカードをどこまで覚えたか
    # 一覧で oncall に必要なコマンドに付けている印を、ここにも付けて、この行と結び付ける
    oncall = f"[d.oncall]{design.active.ONCALL}[/]"
    ready = load_links().ready
    done = sum(1 for card_id in ready if progress.is_learned(card_id))
    if done == len(ready):
        footer.append("  [d.done]oncall への準備ができました。[/d.done][d.muted]障害対応ゲーム oncall で、覚えたコマンドを実戦に使えます。[/d.muted]")
    else:
        footer.append(gauge(labels[1], done, len(ready), lead=oncall))
    footer.append(
        f"\n  [d.done]{design.active.CHECK}[/] [d.muted]おぼえた[/]  [d.review]{design.active.DUE}[/] [d.muted]復習どき[/]"
        f"  {oncall} [d.muted]oncall で使う[/]"
    )

    # ページを送ると変わる部分(カードの一覧とページの案内)は枠で囲み、いつも同じ内容の部分と見分けられるようにする。
    #
    # 一覧に使える行数は、表示の行数から、ロゴと説明、枠の上下の線、ページの案内(上の空行 1 行、案内 2 行)、
    # 枠の下の空行、下の集計、カーソルのいる 1 行を引いた残り。空行をどれだけ入れるかは、行数の余裕で 3 段階に分ける
    #   roomy:   ロゴの下・説明の下・枠の下を 2 行ずつ空ける。打ち間違えたときの一言が出ても流れない余白も残す
    #   compact: 枠の外の空行をなくす(枠の線が区切りになる)
    #   tight:   さらに、案内の上の空行も省く
    used = _line_count(console, footer) + FRAME_ROWS + 3 + 1
    available = rows - _line_count(console, header_lines(True)) - used - 2 - MENU_SPARE_ROWS
    roomy = available >= MIN_MENU_ROWS
    if not roomy:
        available = rows - _line_count(console, header_lines(False)) - used
    tight = available < MIN_MENU_ROWS
    if tight:
        available += 1
    pages = _menu_pages(cards, progress, on, console.width - FRAME_COLUMNS, max(MIN_MENU_ROWS, available))
    page %= len(pages)

    list_width = max(row.cell_len for rows_of_page in pages for row in rows_of_page)
    body = list(pages[page])
    if len(pages) > 1:
        # ページの案内は 2 行に分け、どちらも一覧の幅の中央に置く
        if not tight:
            body.append(Text())
        for guide in (f"[d.title]ページ {page + 1}/{len(pages)}[/]", "[d.muted]← → で移動[/d.muted]"):
            guide = Text.from_markup(guide)
            body.append(Text(" " * max(0, (list_width - guide.cell_len) // 2)) + guide)
    for row in body:
        row.no_wrap = True

    for line in header_lines(roomy):
        console.print(line, highlight=False)
    # 枠の幅は、どのページでも同じにする
    console.print(
        Panel(
            Group(*body),
            title=f"[d.frame.title]コマンド一覧[/]",
            title_align="left",
            box=design.active.BOX,
            border_style="d.frame",
            padding=(0, 1),
            width=list_width + FRAME_COLUMNS,
        )
    )
    if roomy:
        console.print("\n")
    for line in footer:
        console.print(line, highlight=False)
    return page, len(pages)


def _ask(
    screen: Screen,
    *choices: str,
    shortcuts: dict[str, str] | None = None,
    placeholder: str = "",
    redraw_on_resize: bool = False,
) -> str | None:
    """選べる操作を入力欄の上に出して、入力欄から 1 行読む。Ctrl-D や Ctrl-C なら None。

    redraw_on_resize が True なら、端末の大きさが変わったときに RESIZED を返す。
    """
    screen.set_status(*choices)
    answer = screen.read_line(shortcuts=shortcuts, placeholder=placeholder, redraw_on_resize=redraw_on_resize)
    return None if answer is None else answer.strip().lower()


@dataclass(frozen=True)
class Plan:
    """次に開くカードと、その開き方。"""

    card: Card
    review: bool  # True なら説明と試し打ちを飛ばして、お題だけを出す

    @property
    def label(self) -> str:
        kind = f"[d.review]復習[/]" if self.review else "新しいコマンド"
        return f"{kind} [d.command]{self.card.command}[/]"


def _recommend(cards: list[Card], progress: Progress, on: date, after: Card | None = None) -> Plan | None:
    """おすすめの次の 1 枚。復習の時期が来たカードを先に、なければまだ覚えていないカードを出す。"""
    by_id = {card.id: card for card in cards}
    if due := progress.due(list(by_id), on):
        return Plan(by_id[due[0]], review=True)
    start = cards.index(after) + 1 if after else 0
    for card in cards[start:] + cards[:start]:
        if not progress.is_learned(card.id):
            return Plan(card, review=False)
    return None


def _choose(
    screen: Screen, console: Console, cards: list[Card], recommended: Plan | None, paged: bool
) -> Plan | str | None:
    """一覧で次に開くカードを選ぶ。

    ページを送るなら NEXT_PAGE / PREVIOUS_PAGE、チュートリアルを見るなら TUTORIAL、終了なら None を返す。
    """
    choices = [f"[d.key]Enter[/d.key] {recommended.label}"] if recommended else []
    if paged:
        choices.append("[d.key]← →[/d.key] ページ")
    while True:
        # コマンドの選び方は、打つ場所である入力欄の中に薄く出しておく
        answer = _ask(
            screen,
            *choices,
            f"[d.key]{tutorial.WORD}[/] 使い方",
            "[d.key]exit[/d.key] 終了",
            shortcuts=PAGE_KEYS if paged else None,
            placeholder=MENU_PLACEHOLDER,
            redraw_on_resize=True,
        )
        if answer == RESIZED:
            return REDRAW
        if answer is None or answer == "exit":
            return None
        if answer == tutorial.WORD:
            return TUTORIAL
        if paged and answer in {NEXT_PAGE, PREVIOUS_PAGE}:
            return answer
        if not answer and recommended:
            return recommended
        # 自分で選んだカードは、覚えたものでも説明から通して開く
        if named := [card for card in cards if answer in {card.command, card.id}]:
            return Plan(named[0], review=False)
        numbered = "番号では選べません。" if answer.isdigit() else ""
        console.print(f"[d.error]{numbered}一覧にあるコマンド(ls など)を入力してください。[/d.error]")


def _after_card(screen: Screen, upcoming: Plan | None) -> str:
    """カードを終えたあとの行き先を尋ねる。"next" / "again" / MENU / QUIT を返す。"""
    # 次の一手は、お題が出ていた場所(入力欄の下)に大きく出す
    lines = [f" [d.heading]{design.active.HEADING_MARK} 次へ[/]"]
    if upcoming:
        lines.append(f"   [d.key]Enter[/d.key] で{upcoming.label} へ進む")
    lines.append("   [d.key]r[/d.key] もう一度   [d.key]title[/d.key] トップページに戻る   [d.key]exit[/d.key] 終了")
    screen.set_task(lambda width: Text.from_markup("\n".join(lines)))
    try:
        while True:
            answer = _ask(screen)
            # どの場面でも、title はトップページ(一覧)に戻る、exit はアプリを終了する
            if answer is None or answer == "exit":
                return QUIT
            if answer == "title" or (not answer and not upcoming):
                return MENU
            if not answer:
                return "next"
            if answer in {"r", "retry"}:
                return "again"
    finally:
        screen.set_task(None)


def _show_recorded(console: Console, card: Card, recorded: Recorded) -> None:
    """正解を記録した結果(定着度と次の復習の時期)を知らせる。"""
    when = _in_days(recorded.days)
    if recorded.first_time:
        console.print(f"\n  [d.muted]{_dots(recorded.level)}  忘れかけた頃にもう一度。次の復習は{when}です。[/d.muted]")
        if missions := load_links().missions_using(card.id):
            titles = "".join(f"「{title}」" for title in missions)
            console.print(f"  [d.muted]{design.active.NOTE_MARK} 障害対応ゲーム oncall の{titles}で、実際の調査に使います。[/d.muted]")
    elif recorded.clean:
        console.print(f"\n  [d.done]{_dots(recorded.level)}  定着してきました。[/d.done][d.muted]次の復習は{when}です。[/d.muted]")
    else:
        console.print(f"\n  [d.muted]{_dots(recorded.level)}  もう少しです。{when}にもう一度出します。[/d.muted]")


def _reset_progress(console: Console) -> bool:
    """確認のうえセーブデータを消す。取りやめたら False。"""
    path = default_path()
    if not path.exists():
        console.print("セーブデータはまだありません。")
        return True
    answer = console.input(f"セーブデータ {path} を消します。よろしいですか? ([d.key]y[/d.key]/N) > ")
    if answer.strip().lower() != "y":
        console.print("取りやめました。")
        return False
    path.unlink()
    console.print("セーブデータを消しました。\n")
    return True


def _confirm_guard(console: Console) -> bool:
    """隔離モードを使えなかったことを知らせ、簡易モードで始めてよいかを尋ねる。"""
    console.print(
        "[d.warn]隔離モードで実行できなかったので、簡易モードで実行します。[/]\n"
        "[d.muted]簡易モードは、練習用の場所の外への変更を止めますが、完全な隔離ではありません。[/]"
    )
    answer = console.input("よろしいですか? ([d.key]y[/]/n) > ")
    if answer.strip().lower() != "y":
        console.print("取りやめました。")
        return False
    return True


def _exit_on_signal(signum: int, _frame) -> None:
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    design.use(args.design or ("retro" if args.retro else design.default_name()))
    console = Console(theme=design.theme())

    if args.retro_install or args.retro_remove:
        console.print(retro.update_profile(install=args.retro_install), highlight=False)
        return 0
    # --retro: レトロなタブを開いて、そちらで動かす(フォントや色味は端末の側で決まるため)。
    # すでにそのタブの中にいるときは、そのままレトロのデザインで描く
    if args.retro and not retro.inside_tab():
        arguments = [a for a in (sys.argv[1:] if argv is None else argv)]
        if retro.can_open_tab() and retro.open_tab(arguments):
            console.print("レトロなデザインのタブで day-one を開きました。")
            return 0
        console.print(
            "[d.warn]レトロなタブを開けなかったので、いまの端末のまま、レトロのデザインで動かします。[/]\n"
            "[d.muted]フォントや背景の色まで変えるには、先に dayone --retro-install を実行してください。[/]"
        )

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        console.print("[d.error]dayone は対話的なターミナルで起動してください。[/d.error]")
        return 2
    if args.mode == sandbox.ISOLATED and not sandbox.isolation_available():
        console.print(
            "[d.error]この環境では隔離モードを使えません(unshare による名前空間の作成に失敗)。[/d.error]\n"
            "--mode guard で簡易モードを使うか、--mode を外して自動選択にしてください。"
        )
        return 2

    # 端末を閉じられたときも、使い捨てディレクトリの後始末(finally)を走らせる
    for signum in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(signum, _exit_on_signal)

    if args.reset_progress and not _reset_progress(console):
        return 0

    mode = sandbox.resolve_mode(args.mode)
    # 隔離できない環境で自動的に簡易モードになるときは、守りが弱くなるので、始める前に確かめる
    if args.mode == sandbox.AUTO and mode == sandbox.GUARD and not _confirm_guard(console):
        return 0
    cards = load_cards()
    rng = random.Random(args.seed)
    progress = Progress.load()
    if progress.quarantined:
        console.print(
            "[d.warn]セーブデータを読み込めなかったため、新しく始めます。"
            f"元のファイルは {progress.quarantined} に残してあります。[/]"
        )

    # ここから先は、画面を「表示・入力欄・ステータスバー」に分けて使う
    with Screen(console) as screen:
        # 初めて起動したときだけ、最初にチュートリアルを見せる。途中でやめても、見せたものとして記録する
        if not progress.tutorial_seen:
            progress.mark_tutorial_seen()
            if not tutorial.run(screen, console, mode):
                console.print("おつかれさまでした。")
                return 0
        page = 0
        while True:
            screen.clear()  # カードから戻ったときも、一覧だけを表示する
            page, pages = _show_menu(console, screen.rows, cards, progress, mode, today(), page)
            plan = _choose(screen, console, cards, _recommend(cards, progress, today()), paged=pages > 1)
            if plan == TUTORIAL:
                if not tutorial.run(screen, console, mode):
                    console.print("おつかれさまでした。")
                    return 0
                continue
            if plan == REDRAW:
                continue
            if isinstance(plan, str):  # カードを選ばず、ページを送る
                page += 1 if plan == NEXT_PAGE else -1
                continue
            # 選んだカードから、利用者が一覧に戻るか終了するまで続けて進める
            while plan is not None:
                screen.clear()  # 一覧や前のカードの表示を消して、このカードの内容だけにする
                outcome = Lesson(
                    plan.card,
                    console,
                    mode,
                    rng,
                    review=plan.review,
                    screen=screen,
                ).play()
                if outcome.title:  # カードの途中で title と打った。トップページ(一覧)に戻る
                    break
                if not outcome.cleared:  # カードの途中で exit と打った。アプリを終了する
                    plan = None
                    break
                _show_recorded(console, plan.card, progress.record(plan.card.id, clean=outcome.clean, on=today()))
                console.print()
                upcoming = _recommend(cards, progress, today(), after=plan.card)
                choice = _after_card(screen, upcoming)
                if choice == MENU:
                    break
                if choice == QUIT:
                    plan = None
                elif choice == "next":
                    plan = upcoming
            if plan is None:
                console.print("おつかれさまでした。")
                return 0
