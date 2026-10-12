"""デザイン(通常とレトロ)が、互いに独立したまま同じ項目をそろえていること。"""

import io
from datetime import date
from pathlib import Path

import pytest
from rich.console import Console

from dayone import cli, design, sandbox
from dayone.card import load_cards
from dayone.designs import normal, retro
from dayone.progress import Progress


@pytest.fixture(autouse=True)
def back_to_normal():
    yield
    design.use(normal.NAME)


def test_both_designs_define_the_same_items():
    design.check()


def test_designs_do_not_share_anything():
    """片方のファイルが、もう片方や共通の定義を読み込んでいないこと(変えても互いに影響しない)。"""
    for module in (normal, retro):
        source = Path(module.__file__).read_text()
        imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        assert imports == ["from rich import box"], imports
    assert normal.STYLES is not retro.STYLES


@pytest.mark.parametrize("name", sorted(design.DESIGNS))
def test_top_page_renders_in_each_design(name, tmp_path):
    design.use(name)
    out = io.StringIO()
    console = Console(file=out, theme=design.theme(), width=100, force_terminal=True, color_system="standard")
    cli._show_menu(console, 40, load_cards(), Progress.load(tmp_path / "p.json"), sandbox.ISOLATED, date(2026, 10, 12), 0)
    text = out.getvalue()
    assert "コマンド一覧" in text and "pwd" in text
    assert ("隔離モード" in text) == design.active.SHOW_MODE  # 実行方式の行を出すかどうかは、デザインが決める
    # 記号は、そのデザインのものだけが出る
    other = retro if name == normal.NAME else normal
    assert design.active.BAR_OFF in text and other.BAR_OFF not in text


def test_retro_uses_only_ascii_symbols():
    symbols = [value for key, value in vars(retro).items() if key.isupper() and isinstance(value, str) and key != "NAME"]
    assert all(symbol.isascii() for symbol in symbols)


def test_retro_logo_has_no_backslash():
    """日本語のフォントでは \\ が円記号で表示されるので、レトロのロゴには使わない。"""
    assert "\\" not in retro.LOGO
