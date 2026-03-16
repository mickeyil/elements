from pathlib import Path

import pytest

from elemctl.library import ArtifactCache, ProgramLibrary
from elemctl.program_metadata import extract_metadata, extract_strips


def test_extract_metadata_valid_source():
    beat, duration = extract_metadata(
        "BEAT = 0.5\nDURATION = 32\n",
        "demo.py",
    )

    assert beat == 0.5
    assert duration == 32.0


def test_extract_metadata_missing_beat_raises():
    with pytest.raises(ValueError, match='missing BEAT'):
        extract_metadata("DURATION = 10\n", "demo.py")


def test_extract_metadata_expression_duration_rejected():
    with pytest.raises(ValueError, match='DURATION must be a numeric literal'):
        extract_metadata("BEAT = 1.0\nDURATION = 8 * 32\n", "demo.py")


def test_extract_strips_valid_literal_calls():
    strips = extract_strips(
        "from elements.dsl import strip as led_strip\n"
        "left = led_strip('left', length=5)\n"
        "right = led_strip('right', length=5)\n",
        "demo.py",
    )

    assert strips == ['left', 'right']


def test_extract_strips_dynamic_name_returns_none():
    strips = extract_strips(
        "from elements.dsl import strip\n"
        "strip_id = 'main'\n"
        "main = strip(strip_id)\n",
        "demo.py",
    )

    assert strips is None


def test_program_library_rescan_valid_programs(tmp_path):
    (tmp_path / 'alpha.py').write_text("BEAT = 1.0\nDURATION = 2.0\n")
    (tmp_path / 'beta.py').write_text("BEAT = 0.5\nDURATION = 8\n")

    library = ProgramLibrary(str(tmp_path))
    entries = library.list_programs()

    assert [entry.program_id for entry in entries] == ['alpha', 'beta']
    assert entries[0].beat == 1.0
    assert entries[0].duration == 2.0
    assert entries[0].source_hash is not None
    assert entries[0].error is None
    assert entries[0].strips is None


def test_program_library_rescan_extracts_literal_strip_names(tmp_path):
    (tmp_path / 'alpha.py').write_text(
        "from elements.dsl import strip\n"
        "BEAT = 1.0\n"
        "DURATION = 2.0\n"
        "main = strip('main')\n"
    )

    library = ProgramLibrary(str(tmp_path))
    entry = library.get('alpha')

    assert entry is not None
    assert entry.error is None
    assert entry.strips == ['main']


def test_program_library_rescan_dynamic_strip_name_keeps_unknown_summary(tmp_path):
    (tmp_path / 'alpha.py').write_text(
        "from elements.dsl import strip\n"
        "BEAT = 1.0\n"
        "DURATION = 2.0\n"
        "strip_id = 'main'\n"
        "main = strip(strip_id)\n"
    )

    library = ProgramLibrary(str(tmp_path))
    entry = library.get('alpha')

    assert entry is not None
    assert entry.error is None
    assert entry.strips is None


def test_program_library_rescan_unreadable_file(tmp_path, monkeypatch):
    path = tmp_path / 'alpha.py'
    path.write_text("BEAT = 1.0\nDURATION = 2.0\n")
    original = Path.read_text

    def failing_read_text(self, *args, **kwargs):
        if self == path:
            raise OSError('permission denied')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', failing_read_text)

    library = ProgramLibrary(str(tmp_path))
    entry = library.get('alpha')

    assert entry is not None
    assert entry.source is None
    assert entry.source_hash is None
    assert entry.error == 'cannot read file: permission denied'


def test_program_library_rescan_broken_metadata_keeps_source(tmp_path):
    (tmp_path / 'alpha.py').write_text("BEAT = 1.0\n")

    library = ProgramLibrary(str(tmp_path))
    entry = library.get('alpha')

    assert entry is not None
    assert entry.source == "BEAT = 1.0\n"
    assert entry.source_hash is not None
    assert entry.beat is None
    assert entry.duration is None
    assert entry.error == 'missing DURATION'


def test_program_library_rescan_missing_dir_is_empty(tmp_path):
    library = ProgramLibrary(str(tmp_path / 'missing'))

    assert library.list_programs() == []


def test_program_library_rescan_picks_up_new_and_changed_files(tmp_path):
    path = tmp_path / 'alpha.py'
    path.write_text("BEAT = 1.0\nDURATION = 2.0\n")
    library = ProgramLibrary(str(tmp_path))
    first = library.get('alpha')
    assert first is not None

    (tmp_path / 'beta.py').write_text("BEAT = 0.5\nDURATION = 4.0\n")
    path.write_text("BEAT = 1.0\nDURATION = 3.0\n")
    library.rescan()

    entries = library.list_programs()
    assert [entry.program_id for entry in entries] == ['alpha', 'beta']
    assert library.get('alpha').duration == 3.0
    assert library.get('alpha').source_hash != first.source_hash


def test_program_library_publish_creates_program_in_missing_dir(tmp_path):
    root = tmp_path / 'missing'
    library = ProgramLibrary(str(root))

    entry = library.publish(
        'ambient',
        "from elements.dsl import strip\nBEAT = 1.0\nDURATION = 4.0\nmain = strip('main')\n",
    )

    assert (root / 'ambient.py').read_text() == (
        "from elements.dsl import strip\nBEAT = 1.0\nDURATION = 4.0\nmain = strip('main')\n"
    )
    assert entry.program_id == 'ambient'
    assert entry.error is None
    assert entry.strips == ['main']
    assert library.get('ambient') == entry


def test_program_library_publish_replaces_existing_program(tmp_path):
    path = tmp_path / 'ambient.py'
    path.write_text("BEAT = 1.0\nDURATION = 4.0\n")
    library = ProgramLibrary(str(tmp_path))
    first = library.get('ambient')
    assert first is not None

    updated = library.publish('ambient', "BEAT = 1.0\nDURATION = 8.0\n")

    assert path.read_text() == "BEAT = 1.0\nDURATION = 8.0\n"
    assert updated.duration == 8.0
    assert updated.source_hash != first.source_hash
    assert library.list_programs() == [updated]


def test_program_library_publish_broken_source_rejected(tmp_path):
    library = ProgramLibrary(str(tmp_path))

    with pytest.raises(ValueError, match='missing DURATION'):
        library.publish('ambient', "BEAT = 1.0\n")

    assert not (tmp_path / 'ambient.py').exists()
    assert library.get('ambient') is None


def test_program_library_publish_broken_source_does_not_clobber_existing(tmp_path):
    library = ProgramLibrary(str(tmp_path))
    library.publish('ambient', "BEAT = 1.0\nDURATION = 4.0\n")

    with pytest.raises(ValueError, match='missing DURATION'):
        library.publish('ambient', "BEAT = 1.0\n")

    entry = library.get('ambient')
    assert entry is not None
    assert entry.error is None
    assert entry.duration == 4.0
    assert (tmp_path / 'ambient.py').read_text() == "BEAT = 1.0\nDURATION = 4.0\n"


def test_program_library_publish_invalid_program_id_rejected(tmp_path):
    library = ProgramLibrary(str(tmp_path))

    with pytest.raises(ValueError, match='program_id may only contain'):
        library.publish('../escape', "BEAT = 1.0\nDURATION = 2.0\n")

    assert library.list_programs() == []


def test_program_library_get_unknown_returns_none(tmp_path):
    library = ProgramLibrary(str(tmp_path))

    assert library.get('missing') is None


def test_artifact_cache_hit_and_miss():
    cache = ArtifactCache()
    manifest = object()

    cache.put('hash-a', 'topology-a', manifest)

    assert cache.get('hash-a', 'topology-a') is manifest
    assert cache.get('hash-b', 'topology-a') is None
    assert cache.get('hash-a', 'topology-b') is None
