"""kospex.github_export: per-repo `kgit github` exports write as they go.

A file without `.partial` is always a complete run; a stopped run keeps the
repos it finished in `.partial` files and leaves any previous complete file alone.
"""
import json

import pytest

import kospex.github_export as GE


def lines(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]


class TestRunFiles:
    def test_needs_a_path(self):
        with pytest.raises(ValueError):
            GE.RunFiles()

    def test_single_file_readable_before_the_run_finishes(self, tmp_path):
        out = tmp_path / "p.jsonl"
        with GE.RunFiles(out) as files:
            files.add_repo([{"repo": "a"}])
            assert lines(tmp_path / "p.jsonl.partial") == [{"repo": "a"}]
            assert not out.exists()

    def test_finish_renames_every_partial(self, tmp_path):
        a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
        with GE.RunFiles(a, b) as files:
            files.add_repo([{"n": 1}, {"n": 2}], [{"s": 1}])
            files.finish()
        assert lines(a) == [{"n": 1}, {"n": 2}] and lines(b) == [{"s": 1}]
        assert sorted(p.name for p in tmp_path.iterdir()) == ["a.jsonl", "b.jsonl"]

    def test_unfinished_run_keeps_partial_and_old_file(self, tmp_path):
        out = tmp_path / "p.jsonl"
        out.write_text("previous\n")
        with pytest.raises(KeyboardInterrupt):
            with GE.RunFiles(out) as files:
                files.add_repo([{"repo": "a"}])
                raise KeyboardInterrupt
        assert out.read_text() == "previous\n"
        assert lines(tmp_path / "p.jsonl.partial") == [{"repo": "a"}]

    def test_counts_repos_and_lines_per_file(self, tmp_path):
        with GE.RunFiles(tmp_path / "a", tmp_path / "b") as files:
            files.add_repo([{}, {}], [{}])
            files.add_repo([], [{}])
            assert files.repos == 2 and files.lines == [2, 2]

    def test_wrong_number_of_line_lists_refused(self, tmp_path):
        with GE.RunFiles(tmp_path / "a", tmp_path / "b") as files:
            with pytest.raises(ValueError):
                files.add_repo([{}])

    def test_unicode_kept_and_newline_terminated(self, tmp_path):
        out = tmp_path / "p.jsonl"
        with GE.RunFiles(out) as files:
            files.add_repo([{"error": "café"}])
            files.finish()
        text = out.read_text(encoding="utf-8")
        assert "café" in text and text.endswith("\n")


class TestStopMessage:
    def test_one_file(self, tmp_path):
        with GE.RunFiles(tmp_path / "p.jsonl") as files:
            files.add_repo([{}])
            message = GE.stop_message("Interrupted", files, 3, "p.jsonl")
        assert message.startswith("Interrupted. Stopped after 1 of 3 repos: those are in ")
        assert message.count(".partial") == 1
        assert "p.jsonl was not written. Rerun to get every repo." in message

    def test_two_files_joined_with_and(self, tmp_path):
        with GE.RunFiles(tmp_path / "a.jsonl", tmp_path / "a-status.jsonl") as files:
            message = GE.stop_message("Interrupted", files, 3, "a.jsonl")
        assert (f"{tmp_path / 'a.jsonl.partial'} and {tmp_path / 'a-status.jsonl.partial'}"
                in message)
