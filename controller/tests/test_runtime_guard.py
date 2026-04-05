from .conftest import _proc_argv, _runtime_conflict_label


class TestRuntimeGuard:
    def test_proc_argv_splits_null_delimited_cmdline(self):
        raw = b'/usr/bin/python3\0-m\0elemctl\0sim\0--foo\0'
        assert _proc_argv(raw) == [
            '/usr/bin/python3',
            '-m',
            'elemctl',
            'sim',
            '--foo',
        ]

    def test_classifies_network_sim(self):
        assert _runtime_conflict_label(['/tmp/build/network_sim', '--tcp-port', '5001']) == 'network_sim'

    def test_classifies_elemctl_sim(self):
        assert _runtime_conflict_label(['elemctl', 'sim']) == 'elemctl sim'
        assert _runtime_conflict_label(['/home/mickey/dev/elements/elemctl', 'sim']) == 'elemctl sim'
        assert _runtime_conflict_label(['python3', '-m', 'elemctl.sim']) == 'elemctl sim'
        assert _runtime_conflict_label(['python3', '-m', 'elemctl', 'sim']) == 'elemctl sim'
        assert (
            _runtime_conflict_label(
                ['/repo/local/venv/bin/python', '/repo/elements/elemctl', 'sim']
            )
            == 'elemctl sim'
        )

    def test_classifies_elemctl_server(self):
        assert _runtime_conflict_label(['elemctl', 'server']) == 'elemctl server'
        assert (
            _runtime_conflict_label(['/home/mickey/dev/elements/elemctl', 'server'])
            == 'elemctl server'
        )
        assert _runtime_conflict_label(['python3', '-m', 'elemctl.server']) == 'elemctl server'
        assert _runtime_conflict_label(['python3', '-m', 'elemctl', 'server']) == 'elemctl server'
        assert (
            _runtime_conflict_label(
                ['/repo/local/venv/bin/python', '/repo/elements/elemctl', 'server']
            )
            == 'elemctl server'
        )

    def test_ignores_non_conflicting_processes(self):
        assert _runtime_conflict_label(['elemctl', 'tui']) is None
        assert _runtime_conflict_label(['elemctl', 'web']) is None
        assert _runtime_conflict_label(['elemctl', 'run']) is None
        assert _runtime_conflict_label(['python3', '-m', 'elemctl.tui']) is None
