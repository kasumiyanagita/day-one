"""チュートリアル。アプリの使い方を、数ページで案内する。

初めて起動したときに 1 回だけ自動で出す。あとからは、トップページで `tutorial` と打てば見られる。
文章はどのデザインでも同じで、記号と色だけを、いま使っているデザインに合わせる。
"""

from __future__ import annotations

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from dayone import design, sandbox
from dayone.prose import MAX_WIDTH, PANEL_CHROME, render_prose
from dayone.screen import Screen

WORD = "tutorial"  # トップページで打つと、チュートリアルを開く言葉
NEXT, PREVIOUS = "next", "previous"
# 何も打っていないときの矢印キー。→ で次、← で前のページへ移る
KEYS = {"\x1b[C": NEXT, "\x1bOC": NEXT, "\x1b[D": PREVIOUS, "\x1bOD": PREVIOUS}


def _pages(mode: str) -> list[tuple[str, list[str | Text]]]:
    """(ページの見出し, 中身) の並び。中身の文字列は文章として折り返し、Text はそのまま出す。"""
    look = design.active
    safety = (
        "打ったコマンドは本物のシェルで動きます。ただし、練習用に用意した場所の中だけで動くので、"
        "何を打っても、あなたのパソコンのファイルが変わることはありません。安心して試してください。"
        if mode == sandbox.ISOLATED
        else "打ったコマンドは本物のシェルで動きます。練習用の場所の外は変更できないようにしてありますが、"
        "完全に切り離されてはいません(簡易モード)。案内されたコマンドを中心に試してください。"
    )
    rule = look.RULE
    screen_map = [
        Text.from_markup("  [d.muted](説明や、打ったコマンドの結果が、ここに流れます)[/]"),
        Text(),
        Text.from_markup(f"  [d.rule]{rule}[/] [d.command]ls[/] [d.rule]{rule * 12}[/] [d.key]hint[/] ヒント [d.rule]{rule}[/]   [d.muted]いまのコマンドと、使える言葉[/]"),
        Text.from_markup("  [d.prompt.path]~[/] [d.prompt.mark]$[/] ls -l                    [d.muted]入力欄。ここに打って Enter を押します[/]"),
        Text.from_markup(f"  [d.rule]{rule * 28}[/]"),
        Text.from_markup(f"   [d.heading]{look.HEADING_MARK} 打ってみよう[/] [d.muted]1/3[/]            [d.muted]いまやること。ここを読んで打ちます[/]"),
    ]
    return [
        (
            "ようこそ",
            [
                "day-one は、Linux のコマンドを 1 つずつ、実際に打って覚えるアプリです。",
                "1 つのコマンドにつき、**説明を読む → 打ってみる → お題を解く** を、1〜2 分で進めます。",
                safety,
            ],
        ),
        (
            "画面の見方",
            [
                "練習の画面は、上から 3 つに分かれています。いちばん下に、いまやることが出たままになります。",
                *screen_map,
            ],
        ),
        (
            "練習の進め方",
            [
                "1. トップページで **Enter** を押すと、おすすめのコマンドが始まります。一覧にある名前(`cd` など)を打って選ぶこともできます。\n"
                "2. **打ってみよう**: 下に出る案内のとおりに打つと、次へ進みます。ほかのコマンドを自由に試しても構いません。\n"
                "3. **お題**: 自分で考えて打ちます。結果が合っていれば正解です。書き方は 1 通りでなくても通ります。",
                f"正解したコマンドには、一覧で「{look.CHECK}」(おぼえた)が付きます。"
                f"日がたつと「{look.DUE}」(復習どき)に変わるので、もう一度解いて定着させます。",
            ],
        ),
        (
            "困ったとき",
            [
                "練習の途中で、次の言葉を打てます。",
                "1. `hint` … ヒントを見る\n"
                "2. `card` … コマンドの説明を、もう一度表示する\n"
                "3. `title` … トップページに戻る\n"
                "4. `exit` … アプリを終了する",
                "`less` や `vim` のように画面が切り替わるコマンドは、閉じ方が入力欄に出ます。",
                f"このチュートリアルは、トップページで `{WORD}` と打つと、いつでも見られます。",
            ],
        ),
    ]


def _show(console: Console, title: str, blocks: list[str | Text], number: int, total: int) -> None:
    width = min(console.width, MAX_WIDTH)
    inner = width - PANEL_CHROME - 2
    body: list[Text] = []
    in_figure = False
    for block in blocks:
        figure = isinstance(block, Text)
        # 段落のあいだは 1 行空ける。図(Text が続く部分)の中は空けない
        if body and not (figure and in_figure):
            body.append(Text())
        if figure:
            block.no_wrap = True
            body.append(block)
        else:
            body.append(render_prose(block, inner))
        in_figure = figure
    console.print()
    console.print(
        Panel(
            Group(*body),
            title=f"[d.frame.caption]チュートリアル {number}/{total}  {title}[/]",
            title_align="left",
            box=design.active.BOX,
            border_style="d.frame",
            padding=(1, 2),
            width=width,
        )
    )


def run(screen: Screen, console: Console, mode: str) -> bool:
    """チュートリアルを最初のページから見せる。終了(exit)を選んだら False、それ以外は True。"""
    pages = _pages(mode)
    index = 0
    while True:
        screen.clear()
        title, blocks = pages[index]
        _show(console, title, blocks, index + 1, len(pages))
        last = index == len(pages) - 1
        screen.set_status(
            f"[d.key]Enter[/] {'はじめる' if last else '次へ'}",
            "[d.key]← →[/] ページ",
            "[d.key]title[/] トップページに戻る",
            "[d.key]exit[/] 終了",
        )
        answer = screen.read_line(shortcuts=KEYS, placeholder="Enter で次へ" if not last else "Enter でトップページへ")
        answer = None if answer is None else answer.strip().lower()
        if answer is None or answer == "exit":
            return False
        if answer == "title" or (answer == "" and last):
            return True
        if answer == PREVIOUS:
            index = max(0, index - 1)
        elif answer in {"", NEXT}:
            index = min(len(pages) - 1, index + 1)
