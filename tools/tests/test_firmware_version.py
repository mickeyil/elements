"""tools/firmware_version.py against throwaway git repos.

Each test builds a small repo whose layout mirrors the real one (the
paths are what the pathspec cares about) and checks the version the
next firmware build would get as commits land.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import firmware_version as fv

TOOL = Path(__file__).resolve().parent.parent / 'firmware_version.py'

# Commits made here must not depend on the developer's git config
# (identity, signing, hooks).
_GIT_ENV = {
    **os.environ,
    'GIT_CONFIG_GLOBAL': os.devnull,
    'GIT_CONFIG_NOSYSTEM': '1',
    'GIT_AUTHOR_NAME': 'test', 'GIT_AUTHOR_EMAIL': 'test@example.com',
    'GIT_COMMITTER_NAME': 'test', 'GIT_COMMITTER_EMAIL': 'test@example.com',
}


class Repo:
    def __init__(self, root):
        self.root = root
        self.git('init', '-q')
        self._n = 0

    def git(self, *args):
        subprocess.run(['git', '-C', str(self.root), *args], check=True,
                       capture_output=True, env=_GIT_ENV)

    def write(self, rel, text=None):
        """Write rel (unique content by default) without committing."""
        self._n += 1
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text if text is not None else f'change {self._n}\n')

    def commit(self, rel, text=None):
        self.write(rel, text)
        self.git('add', rel)
        self.git('commit', '-q', '-m', f'touch {rel}')

    def version(self):
        return fv.firmware_version(self.root)


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def test_counts_from_the_root_before_the_anchor_is_committed(repo):
    repo.commit('src/core/engine.cpp')
    repo.commit('src/controller/app.cpp')
    repo.write(fv.MAJOR_FILE, '0\n')         # present but untracked
    assert repo.version() == '0.2+d'


def test_anchor_commit_builds_as_zero_minor(repo):
    repo.commit('src/core/engine.cpp')
    repo.commit(fv.MAJOR_FILE, '0\n')
    assert repo.version() == '0.0'


def test_only_firmware_commits_move_the_minor(repo):
    repo.commit(fv.MAJOR_FILE, '0\n')
    repo.commit('src/core/engine.cpp')
    repo.commit('src/platform/esp32/main.cpp')
    repo.commit('platformio.ini')
    assert repo.version() == '0.3'

    repo.commit('src/platform/sim/main.cpp')       # sim is excluded
    repo.commit('controller/elemctl/service.py')
    repo.commit('README.md')
    assert repo.version() == '0.3'

    repo.commit('src/platform/device_identity.h')  # shared platform code counts
    assert repo.version() == '0.4'


def test_dirty_firmware_sources_mark_the_version(repo):
    repo.commit(fv.MAJOR_FILE, '0\n')
    repo.commit('src/core/engine.cpp')
    assert repo.version() == '0.1'

    repo.write('src/core/engine.cpp')                # modified
    assert repo.version() == '0.1+d'
    repo.git('checkout', '--', 'src/core/engine.cpp')
    assert repo.version() == '0.1'

    repo.write('src/controller/new_file.cpp')        # untracked counts too
    assert repo.version() == '0.1+d'


def test_changes_outside_the_firmware_leave_it_clean(repo):
    repo.commit(fv.MAJOR_FILE, '0\n')
    repo.commit('src/core/engine.cpp')
    repo.write('src/platform/sim/main.cpp')
    repo.write('controller/elemctl/service.py')
    assert repo.version() == '0.1'


def test_major_bump_resets_the_minor(repo):
    repo.commit(fv.MAJOR_FILE, '0\n')
    repo.commit('src/core/engine.cpp')
    repo.commit('src/core/engine.cpp')
    assert repo.version() == '0.2'

    # The working-tree major is used at once; the edit itself is dirty.
    repo.write(fv.MAJOR_FILE, '1\n')
    assert repo.version() == '1.2+d'

    repo.git('commit', '-q', '-am', 'bump major')
    assert repo.version() == '1.0'
    repo.commit('src/controller/app.cpp')
    assert repo.version() == '1.1'


def test_outside_a_checkout_is_unknown(tmp_path):
    (tmp_path / 'src/platform/esp32').mkdir(parents=True)
    (tmp_path / fv.MAJOR_FILE).write_text('0\n')
    assert fv.firmware_version(tmp_path) == 'unknown'


def test_missing_or_bad_major_is_unknown(repo):
    repo.commit('src/core/engine.cpp')
    assert repo.version() == 'unknown'
    repo.commit(fv.MAJOR_FILE, 'one\n')
    assert repo.version() == 'unknown'


def test_cli_prints_the_version(repo):
    repo.commit(fv.MAJOR_FILE, '3\n')
    repo.commit('src/core/engine.cpp')
    out = subprocess.run([sys.executable, str(TOOL), str(repo.root)], check=True,
                         capture_output=True, text=True).stdout
    assert out == '3.1\n'
