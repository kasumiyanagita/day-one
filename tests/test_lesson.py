"""カードの進行: 寄り道、ヒント、間違い、中断。"""

from conftest import CARDS, play, walkthrough


def test_exploring_does_not_advance_but_is_allowed(mode):
    card = CARDS["ls"]
    played = play(card, ["pwd", "echo hi", *walkthrough(card)], mode)
    assert played.outcome.cleared
    assert "hi" in played.terminal
    # お手本と違うコマンドが続いたので、進み方を知らせている
    assert "先へ進むには" in played.screen


def test_hint_in_try_step_shows_the_command(mode):
    card = CARDS["cat"]
    played = play(card, ["hint", *walkthrough(card)], mode)
    assert played.outcome.cleared
    assert "cat memo.txt と打ってみましょう" in played.screen
    assert played.outcome.hints == 0  # 試し打ちのヒントは数えない


def test_hints_in_challenge_open_one_at_a_time(mode):
    card = CARDS["ls"]
    steps = walkthrough(card)
    played = play(card, [*steps[:-1], "hint", "hint", "hint", steps[-1]], mode)
    assert played.outcome.cleared
    assert played.outcome.hints == 2
    assert f"ls {played.lesson.card.challenge.solution.split()[1]} と打ちます" in played.screen


def test_right_result_without_the_command_is_not_accepted(mode):
    card = CARDS["pwd"]
    steps = walkthrough(card)
    played = play(card, [*steps[:-1], 'echo "$PWD"'], mode)
    assert not played.outcome.cleared
    assert "結果は合っています" in played.screen
    assert played.outcome.misses == 1


def test_right_command_with_wrong_result_gets_feedback(mode):
    card = CARDS["ls"]
    steps = walkthrough(card)
    played = play(card, [*steps[:-1], "ls -a", steps[-1]], mode)
    assert played.outcome.cleared
    assert "ls は使えています" in played.screen
    assert played.outcome.misses == 1


def test_challenge_can_be_solved_in_another_way(mode):
    card = CARDS["ls"]
    steps = walkthrough(card)
    played = play(card, [*steps[:-1], lambda c: f"cd {c.challenge.solution.split()[1]}", "ls"], mode)
    assert played.outcome.cleared


def test_pwd_challenge_moves_the_player_without_showing_it(mode):
    card = CARDS["pwd"]
    played = play(card, walkthrough(card), mode, seed=4)
    place = played.lesson.card.challenge.before.removeprefix("cd ~/")
    assert played.outcome.cleared
    assert played.terminal.count(place) == 1  # お題の pwd の出力だけ。移動のコマンドは見せない
    assert "cd " not in played.terminal


def test_hidden_command_is_not_in_history(mode):
    card = CARDS["pwd"]
    steps = walkthrough(card)
    played = play(card, [*steps[:-1], "history"], mode)
    assert "cd ~" not in played.terminal
    assert "history -d" not in played.terminal


def test_exit_leaves_the_card(mode):
    played = play(CARDS["cat"], ["exit", "cat memo.txt"], mode)
    assert not played.outcome.cleared


def test_failed_command_does_not_count_as_trying(mode):
    card = CARDS["cat"]
    played = play(card, ["cat no-such-file.txt"], mode)
    assert "打ってみよう 2/2" not in played.screen


def test_exploring_is_not_counted_as_a_wrong_answer(mode):
    card = CARDS["ls"]
    steps = walkthrough(card)
    played = play(card, [*steps[:-1], "pwd", "cat memo.txt", steps[-1]], mode)
    assert played.outcome.cleared and played.outcome.clean
    assert (played.outcome.misses, played.outcome.wrong) == (2, 0)


def test_wrong_answers_and_hints_make_it_not_clean(mode):
    card = CARDS["ls"]
    steps = walkthrough(card)
    assert not play(card, [*steps[:-1], "ls -a", steps[-1]], mode).outcome.clean
    assert not play(card, [*steps[:-1], "hint", steps[-1]], mode).outcome.clean


def test_try_step_needs_the_option_it_is_teaching(mode):
    card = CARDS["ls"]
    played = play(card, ["ls", "ls documents", "ls -a"], mode)
    # 2 手目のお手本は ls -l。別の ls では先へ進まない
    assert "打ってみよう 2/3" in played.screen
    assert "打ってみよう 3/3" not in played.screen

    combined = play(card, ["ls", "ls -la", "ls -a"], mode)
    assert "お題" in combined.screen


def test_cd_challenge_can_be_walked_one_directory_at_a_time(mode):
    card = CARDS["cd"]
    steps = walkthrough(card)

    def first_half(c):
        return "cd " + c.challenge.solution.removeprefix("cd ~/").split("/")[0]

    def second_half(c):
        return "cd " + c.challenge.solution.removeprefix("cd ~/").split("/")[1]

    played = play(card, [*steps[:-1], first_half, second_half], mode)
    assert played.outcome.cleared and played.outcome.clean
    assert "お題と違う" not in played.screen


def test_cd_to_the_wrong_place_gets_feedback(mode):
    card = CARDS["cd"]
    steps = walkthrough(card)
    played = play(card, [*steps[:-1], "cd music", steps[-1]], mode)
    assert played.outcome.cleared and not played.outcome.clean
    assert "いまいる場所が、お題と違う" in played.screen


def test_deleting_the_wrong_file_explains_how_to_start_over(mode):
    card = CARDS["rm"]
    steps = walkthrough(card)
    played = play(card, [*steps[:-1], "rm letter.txt"], mode)
    assert not played.outcome.cleared
    assert "letter.txt がなくなっています" in played.screen and "開き直す" in played.screen


def test_typo_shows_only_the_plain_shell_error(mode):
    played = play(CARDS["cat"], ["catt memo.txt"], mode)
    assert "command not found" in played.terminal
    assert "apt" not in played.terminal


def test_redirect_card_reports_what_is_still_missing(mode):
    card = CARDS["redirect"]
    steps = walkthrough(card)
    played = play(card, [*steps[:-1], "echo x > other.txt", steps[-1]], mode)
    assert played.outcome.cleared
    assert "がまだありません" in played.screen



def test_title_leaves_the_card_for_the_top_page(mode):
    card = CARDS["ls"]
    played = play(card, ["ls", "title", "ls -l"], mode)
    assert played.outcome.title and not played.outcome.cleared
    assert "打ってみよう 3/3" not in played.screen  # title のあとの行は実行されない
