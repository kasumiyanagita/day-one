from dayone.judge import commands_in, compare_files, follows_example, plain, snapshot, tokens
from dayone.shell import _MarkerParser


def test_tokens_ignore_colours_columns_and_line_endings():
    on_terminal = "\x1b[0m\x1b[01;34mdocuments\x1b[0m  memo.txt  \x1b[01;34mmusic\x1b[0m\r\n"
    through_pipe = "documents\nmemo.txt\nmusic\n"
    assert tokens(on_terminal) == tokens(through_pipe)
    assert plain("\x1b]0;title\x07abc\r\n") == "abc\n"


def test_commands_in_reads_each_part_of_the_line():
    assert commands_in("grep apple fruits.txt | wc -l") == {"grep", "wc"}
    assert commands_in("cd docs && ls -l; /usr/bin/cat a.txt") == {"cd", "ls", "cat"}
    assert commands_in("LANG=C sort names.txt") == {"sort"}
    assert commands_in('echo "$(pwd)"') == {"echo", "pwd"}
    assert commands_in("echo ls") == {"echo"}


def _tree(root, spec):
    for rel, content in spec.items():
        path = root / rel
        if content is None:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    return snapshot(root)


def test_compare_files_follows_the_reference_change(tmp_path):
    before = _tree(tmp_path / "before", {"a.txt": "a", "old.txt": "x", "keep.txt": "k"})
    after = _tree(tmp_path / "after", {"a.txt": "a", "keep.txt": "k", "new/b.txt": "b"})

    same = _tree(tmp_path / "same", {"a.txt": "a", "keep.txt": "k", "new/b.txt": "b", "extra.txt": "ok"})
    assert compare_files(same, before, after) is None

    not_deleted = _tree(tmp_path / "p1", {"a.txt": "a", "old.txt": "x", "keep.txt": "k", "new/b.txt": "b"})
    assert "old.txt" in compare_files(not_deleted, before, after)

    not_created = _tree(tmp_path / "p2", {"a.txt": "a", "keep.txt": "k"})
    assert "まだありません" in compare_files(not_created, before, after)

    collateral = _tree(tmp_path / "p3", {"a.txt": "a", "new/b.txt": "b"})
    assert "keep.txt" in compare_files(collateral, before, after)

    wrong_content = _tree(tmp_path / "p4", {"a.txt": "a", "keep.txt": "k", "new/b.txt": "zzz"})
    assert "中身" in compare_files(wrong_content, before, after)

    wrong_kind = _tree(tmp_path / "p5", {"a.txt": "a", "keep.txt": "k", "new": "file"})
    assert "ディレクトリ" in compare_files(wrong_kind, before, after)

    # お手本が触らなかったファイルは、中身が変わっていても問わない
    edited_untouched = _tree(tmp_path / "p6", {"a.txt": "edited", "keep.txt": "k", "new/b.txt": "b"})
    assert compare_files(edited_untouched, before, after) is None


def test_marker_parser_handles_marks_split_across_reads():
    stream = b"hello\x1b]7770;S\x07out\x1b[0mput\x1b]7770;E;0;L2E=;bHM=\x07$ "
    for size in (1, 2, 3, 5, 7, len(stream)):
        parser = _MarkerParser()
        events = []
        for i in range(0, len(stream), size):
            events += parser.feed(stream[i : i + size])
        text = b"".join(payload for kind, payload in events if kind == "text")
        marks = [payload for kind, payload in events if kind == "mark"]
        assert text == b"helloout\x1b[0mput$ "
        assert marks == [b"S", b"E;0;L2E=;bHM="]


def test_follows_example_requires_the_same_options_and_arguments():
    assert follows_example("ls -l", "ls -l")
    assert follows_example("ls -la", "ls -l")
    assert follows_example("ls -a -l", "ls -l")
    assert not follows_example("ls -l documents", "ls -l")  # 引数が増えたら別の操作
    assert not follows_example("cd documents", "cd")
    assert follows_example("echo apple >fruits.txt", "echo apple > fruits.txt")
    assert not follows_example("echo apple > fruits.txt", "echo apple >> fruits.txt")
    assert follows_example("cat ranking.txt|tail -n 3", "cat ranking.txt | tail -n 3")
    assert follows_example("ls --help | head -n 12", "ls --help | head -n 12")
    assert follows_example("cat  -n   memo.txt", "cat -n memo.txt")
    assert follows_example("ls -a", "ls")
    assert not follows_example("ls documents", "ls -l")
    assert not follows_example("ls -a", "ls -l")
    assert not follows_example("cat memo.txt", "cat -n memo.txt")
    assert not follows_example("cat -n todo.txt", "cat -n memo.txt")
