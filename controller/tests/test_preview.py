"""Preview-frame assembly tests (no sockets, no compiler).

The assembler keys on a time-derived frame index, floor(t_program * fps), not
the sim's own frame_index. At 50 fps a frame period is 0.02 s, so t_program
0.02 -> index 1, 0.04 -> index 2. set_program() takes (strip_id, pixel_length)
entries; an rgb blob must be pixel_length * 3 bytes or it is dropped.
"""

from elemctl.preview import PreviewAssembler, _MAX_PENDING_BUCKETS

# One-pixel strips keep the rgb blobs to three bytes in these tests.
RGB = b'\x01\x02\x03'


def test_complete_frame_emitted_in_manifest_order():
    a = PreviewAssembler()
    a.set_program([('left', 1), ('right', 1)], target_fps=50)
    a.add('right', 0.02, b'RGB')
    assert a.drain() == []                 # incomplete: only one strip
    a.add('left', 0.02, b'LGB')

    frames = a.drain()
    assert len(frames) == 1
    assert frames[0].frame_index == 1
    assert frames[0].t_rel == 0.02
    assert frames[0].strips == [b'LGB', b'RGB']   # manifest order, not arrival
    assert a.drain() == []


def test_index_is_floor_of_time_times_fps():
    a = PreviewAssembler()
    a.set_program([('main', 1)], target_fps=50)
    # Render jitter lands t_program just past the frame deadline; floor holds.
    a.add('main', 0.0405, RGB)
    assert a.drain()[0].frame_index == 2


def test_wrong_length_rgb_dropped():
    a = PreviewAssembler()
    a.set_program([('main', 1)], target_fps=50)   # expects 3 bytes
    a.add('main', 0.02, b'\x00\x00')               # 2 bytes
    assert a.drain() == []
    a.add('main', 0.02, RGB)
    assert len(a.drain()) == 1


def test_unknown_strip_ignored():
    a = PreviewAssembler()
    a.set_program([('main', 1)], target_fps=50)
    a.add('ghost', 0.02, RGB)
    a.add('main', 0.02, RGB)
    assert [f.strips for f in a.drain()] == [[RGB]]


def test_mirrored_strip_first_frame_wins():
    a = PreviewAssembler()
    a.set_program([('wall', 1)], target_fps=50)
    a.add('wall', 0.02, b'aaa')
    a.add('wall', 0.02, b'bbb')            # same index, already emitted: ignored
    assert [f.strips for f in a.drain()] == [[b'aaa']]


def test_no_program_or_no_fps_ignores():
    a = PreviewAssembler()
    a.add('main', 0.02, RGB)               # set_program never called
    assert a.drain() == []


def test_stale_future_frame_does_not_block_real_frames():
    # A stale end-of-previous-run frame (high index) must not poison the low
    # indices of the current run; at worst it flashes one spurious frame.
    a = PreviewAssembler()
    a.set_program([('main', 1)], target_fps=50)
    a.add('main', 2.00, RGB)               # index 100, emits once
    a.add('main', 0.02, RGB)               # index 1, still emits
    assert sorted(f.frame_index for f in a.drain()) == [1, 100]


def test_reset_run_clears_partials_and_emitted():
    a = PreviewAssembler()
    a.set_program([('left', 1), ('right', 1)], target_fps=50)
    a.add('left', 0.02, RGB)               # partial
    a.reset_run()
    a.add('right', 0.02, RGB)
    assert a.drain() == []                 # the old 'left' is gone


def test_overflow_caps_pending_buckets():
    a = PreviewAssembler()
    a.set_program([('left', 1), ('right', 1)], target_fps=50)
    for i in range(_MAX_PENDING_BUCKETS + 3):
        a.add('left', i * 0.02, RGB)       # all partial, never complete
    assert len(a._buckets) <= _MAX_PENDING_BUCKETS
