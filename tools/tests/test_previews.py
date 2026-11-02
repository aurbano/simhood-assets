"""The previews branch: one parentless commit, only open pull requests' latest folders."""

import subprocess

import pytest
from assets_lib import previews
from pngs import png


def git(remote, *args):
    return subprocess.run(["git", "--git-dir", str(remote), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def remote(tmp_path):
    path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True)
    return path


def tree(remote):
    return set(git(remote, "ls-tree", "-r", "--name-only", "previews").splitlines())


def files(*names):
    return {n: png() for n in names}


def test_the_first_publish_creates_a_single_parentless_commit(remote):
    r = previews.rebuild(str(remote), lambda: {1}, replace=(1, files("abc1234/1.png")))
    assert r.pushed
    assert tree(remote) == {"README.md", "pr-1/abc1234/1.png"}
    assert git(remote, "rev-list", "--count", "previews") == "1"
    assert git(remote, "rev-parse", "previews") == r.commit
    assert git(remote, "log", "-1", "--format=%P", "previews") == ""


def test_open_pull_requests_are_kept_and_closed_ones_dropped(remote):
    previews.rebuild(str(remote), lambda: {1, 2}, replace=(1, files("aaaaaaa/1.png")))
    previews.rebuild(str(remote), lambda: {1, 2}, replace=(2, files("bbbbbbb/1.png", "bbbbbbb/2.png")))
    assert tree(remote) == {"README.md", "pr-1/aaaaaaa/1.png", "pr-2/bbbbbbb/1.png", "pr-2/bbbbbbb/2.png"}
    # PR 1 closed meanwhile; PR 2 gets a new head: only its latest folder remains
    previews.rebuild(str(remote), lambda: {2}, replace=(2, files("ccccccc/1.png")))
    assert tree(remote) == {"README.md", "pr-2/ccccccc/1.png"}
    assert git(remote, "rev-list", "--count", "previews") == "1"  # history does not pile up
    assert git(remote, "log", "-1", "--format=%P", "previews") == ""


def test_cleanup_drops_the_pull_request_even_if_the_api_still_lists_it(remote):
    previews.rebuild(str(remote), lambda: {1, 2}, replace=(1, files("aaaaaaa/1.png")))
    previews.rebuild(str(remote), lambda: {1, 2}, replace=(2, files("bbbbbbb/1.png")))
    r = previews.rebuild(str(remote), lambda: {1, 2}, drop=1)
    assert r.pushed and tree(remote) == {"README.md", "pr-2/bbbbbbb/1.png"}


def test_nothing_changes_when_the_tree_is_the_same(remote):
    first = previews.rebuild(str(remote), lambda: {1}, replace=(1, files("aaaaaaa/1.png")))
    again = previews.rebuild(str(remote), lambda: {1})
    assert not again.pushed and again.commit == first.commit


def test_cleanup_without_a_branch_creates_nothing(remote):
    r = previews.rebuild(str(remote), lambda: set(), drop=4, create=False)
    assert not r.pushed
    assert git(remote, "branch", "--list", "previews") == ""


def test_a_lost_race_is_retried_from_the_fresh_tree(remote):
    previews.rebuild(str(remote), lambda: {1}, replace=(1, files("aaaaaaa/1.png")))
    calls = []

    def open_now():
        calls.append(1)
        if len(calls) == 1:  # someone else publishes PR 3 between our fetch and our push
            previews.rebuild(str(remote), lambda: {1, 3}, replace=(3, files("ddddddd/1.png")))
        return {1, 2, 3}

    previews.rebuild(str(remote), open_now, replace=(2, files("bbbbbbb/1.png")))
    assert len(calls) == 2
    assert tree(remote) == {"README.md", "pr-1/aaaaaaa/1.png", "pr-2/bbbbbbb/1.png", "pr-3/ddddddd/1.png"}
    assert git(remote, "rev-list", "--count", "previews") == "1"


def test_it_gives_up_after_the_attempts(remote):
    previews.rebuild(str(remote), lambda: {1}, replace=(1, files("aaaaaaa/1.png")))
    n = []

    def always_raced():
        n.append(1)
        previews.rebuild(str(remote), lambda: {1}, replace=(1, files(f"{len(n):07x}/1.png")))
        return {1}

    with pytest.raises(previews.PushLost):
        previews.rebuild(str(remote), always_raced, replace=(1, files("eeeeeee/1.png")), attempts=3)
    assert len(n) == 3


def test_only_generated_file_names_are_written(remote):
    with pytest.raises(ValueError):
        previews.rebuild(str(remote), lambda: {1}, replace=(1, {"../x.png": png()}))
