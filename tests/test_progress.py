import json
import re
from datetime import date, timedelta

from dayone.cli import _recommend
from dayone.card import load_cards
from dayone.progress import INTERVALS, MAX_LEVEL, Progress

DAY = date(2026, 10, 10)


def test_first_clear_is_saved_and_due_tomorrow(tmp_path):
    progress = Progress.load(tmp_path / "data" / "progress.json")
    recorded = progress.record("pwd", clean=True, on=DAY)
    assert (recorded.first_time, recorded.level, recorded.days) == (True, 0, 1)

    reloaded = Progress.load(progress.path)
    assert reloaded.is_learned("pwd") and not reloaded.is_learned("ls")
    assert reloaded.cards["pwd"].due == "2026-10-11"
    assert reloaded.due(["pwd", "ls"], DAY) == []
    assert reloaded.due(["pwd", "ls"], DAY + timedelta(days=1)) == ["pwd"]


def test_clean_reviews_stretch_the_interval_up_to_the_cap(tmp_path):
    progress = Progress.load(tmp_path / "progress.json")
    on = DAY
    progress.record("pwd", clean=True, on=on)
    seen = []
    for _ in range(MAX_LEVEL + 2):
        on = date.fromisoformat(progress.cards["pwd"].due)
        seen.append(progress.record("pwd", clean=True, on=on).days)
    assert seen == [*INTERVALS[1:], INTERVALS[-1], INTERVALS[-1]]
    assert progress.cards["pwd"].level == MAX_LEVEL


def test_struggling_in_review_starts_over(tmp_path):
    progress = Progress.load(tmp_path / "progress.json")
    progress.record("pwd", clean=True, on=DAY)
    progress.record("pwd", clean=True, on=DAY)
    assert progress.cards["pwd"].level == 1

    recorded = progress.record("pwd", clean=False, on=DAY)
    assert (recorded.first_time, recorded.level, recorded.days) == (False, 0, 1)
    assert Progress.load(progress.path).cards["pwd"].reviews == 2


def test_due_cards_come_oldest_first(tmp_path):
    progress = Progress.load(tmp_path / "progress.json")
    progress.record("ls", clean=True, on=DAY + timedelta(days=2))
    progress.record("pwd", clean=True, on=DAY)
    progress.record("cat", clean=True, on=DAY + timedelta(days=30))
    assert progress.due(["pwd", "ls", "cat"], DAY + timedelta(days=5)) == ["pwd", "ls"]


def test_broken_save_is_set_aside_not_overwritten(tmp_path):
    path = tmp_path / "progress.json"
    for broken in ("{ not json", json.dumps({"version": 1, "cards": {"pwd": {"learned": "x", "level": 9, "due": "y"}}})):
        path.write_text(broken)
        progress = Progress.load(path)
        assert progress.cards == {} and progress.quarantined.read_text() == broken
        progress.quarantined.unlink()
    progress.record("pwd", clean=True, on=DAY)
    assert json.loads(path.read_text())["version"] == 1


def test_recommendation_prefers_due_reviews_then_new_cards(tmp_path):
    cards = load_cards()
    progress = Progress.load(tmp_path / "progress.json")

    first = _recommend(cards, progress, DAY)
    assert (first.card.id, first.review) == (cards[0].id, False)

    progress.record(cards[0].id, clean=True, on=DAY)
    following = _recommend(cards, progress, DAY, after=cards[0])
    assert (following.card.id, following.review) == (cards[1].id, False)

    tomorrow = _recommend(cards, progress, DAY + timedelta(days=1))
    assert (tomorrow.card.id, tomorrow.review) == (cards[0].id, True)

    for card in cards:
        progress.record(card.id, clean=True, on=DAY)
    assert _recommend(cards, progress, DAY) is None


def test_menu_pages_show_every_card_once_whatever_the_screen_size(tmp_path):
    from dayone.cli import _menu_pages

    cards = load_cards()
    progress = Progress.load(tmp_path / "progress.json")
    for width in (60, 80, 120):
        for height in (4, 7, 12, 40):
            pages = _menu_pages(cards, progress, DAY, width, height)
            text = "\n".join(row.plain for page in pages for row in page)
            for number, card in enumerate(cards, start=1):
                # 番号、印(おぼえた / 復習どき)、oncall の印、コマンドの名前の順に並ぶ
                found = re.findall(rf"{number:>3} . . {re.escape(card.command)} ", text)
                assert len(found) == 1, (width, height, card.command)
            assert all(row.cell_len <= width for page in pages for row in page)
            assert all(len(page) <= height for page in pages)
    # 十分に広ければ 1 ページに収まり、2 列に並ぶ
    assert len(_menu_pages(cards, progress, DAY, 120, 40)) == 1
    assert len(_menu_pages(cards, progress, DAY, 120, 40)[0]) < len(cards)


def test_oncall_mark_is_on_exactly_the_commands_needed_for_oncall(tmp_path):
    from dayone import design
    from dayone.cli import _menu_pages
    from dayone.links import load_links

    cards = load_cards()
    progress = Progress.load(tmp_path / "progress.json")
    text = "\n".join(row.plain for page in _menu_pages(cards, progress, DAY, 120, 40) for row in page)
    marked = {card.id for card in cards if re.search(rf". {re.escape(design.active.ONCALL)} {re.escape(card.command)} ", text)}
    assert marked == set(load_links().ready)
