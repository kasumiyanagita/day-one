"""全カード共通: お手本どおりに打てば正解になり、試し打ちだけでは正解にならないこと。"""

import random

import pytest

from conftest import CARDS, play, walkthrough

SEEDS = range(6)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("card_id", CARDS)
def test_walkthrough_clears_card(card_id, seed, mode):
    played = play(CARDS[card_id], walkthrough(CARDS[card_id]), mode, seed)
    assert played.outcome.cleared, played.screen + played.terminal
    assert (played.outcome.hints, played.outcome.misses) == (0, 0)
    assert "正解" in played.screen


@pytest.mark.parametrize("card_id", CARDS)
def test_try_steps_alone_do_not_clear(card_id, mode):
    card = CARDS[card_id]
    played = play(card, walkthrough(card)[:-1], mode)
    assert not played.outcome.cleared
    assert "お題" in played.screen and "正解" not in played.screen


@pytest.mark.parametrize("card_id", CARDS)
def test_card_text_is_complete(card_id):
    card = CARDS[card_id]
    assert card.summary and card.when and card.syntax and card.category
    assert card.steps, "試し打ちが 1 手もない"
    assert card.challenge.hints and card.challenge.done
    # 変数の候補をどう選んでも、置き換え残しがないこと
    for seed in range(20):
        filled = card.instantiate(random.Random(seed))
        texts = [filled.challenge.ask, filled.challenge.solution, filled.challenge.before, filled.start]
        texts += [t for s in filled.steps for t in (s.say, s.run, s.after, s.before)]
        texts += [t for f in filled.files for t in (f.path, f.content)]
        assert not any("{{" in text for text in texts)


def test_cards_have_unique_ids_and_commands():
    assert len(CARDS) == len({card.command for card in CARDS.values()})


@pytest.mark.parametrize("seed", SEEDS[:3])
@pytest.mark.parametrize("card_id", CARDS)
def test_review_asks_only_the_challenge(card_id, seed, mode):
    """復習では試し打ちを飛ばすので、最初の状態からお手本だけで正解になること。"""
    played = play(CARDS[card_id], [lambda c: c.challenge.solution], mode, seed, review=True)
    assert played.outcome.cleared and played.outcome.clean, played.screen + played.terminal
    assert "復習" in played.screen and "打ってみよう" not in played.screen


def test_oncall_links_point_at_existing_cards():
    from dayone.links import load_links

    links = load_links()
    referenced = set(links.ready) | {card_id for _, cards in links.missions for card_id in cards}
    assert referenced <= set(CARDS)
    assert links.missions_using("head") == ["深夜の 500 祭り"]
    assert links.missions_using("pwd") == []


def test_categories_are_contiguous_in_card_order():
    """地図は分類ごとにまとめて表示するので、同じ分類のカードは続けて並べる。"""
    categories = [card.category for card in CARDS.values()]
    order = list(dict.fromkeys(categories))
    assert categories == sorted(categories, key=order.index)
    assert len(CARDS) == 38
