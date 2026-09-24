"""Preview-frame assembly tests (no sockets, no compiler).

The assembler keys on (cycle, index) with a time-derived frame index,
t_ms * fps // 1000, not the sim's own frame_index. At 50 fps a frame period
is 20 ms, so t_ms 20 -> index 1, 40 -> index 2. set_program() takes
(strip_id, pixel_length) entries; an rgb blob must be pixel_length * 3 bytes
or it is dropped.
"""

from elemctl.preview import PreviewAssembler, _MAX_PENDING_BUCKETS

# One-pixel strips keep the rgb blobs to three bytes in these tests.
RGB = b'\x01\x02\x03'


def test_complete_frame_emitted_in_manifest_order():
    a = PreviewAssembler()
    a.set_program([('left', 1), ('right', 1)], target_fps=50)
    a.add('right', 0, 20, b'RGB')
    assert a.drain() == []                 # incomplete: only one strip
    a.add('left', 0, 20, b'LGB')

    frames = a.drain()
    assert len(frames) == 1
    assert frames[0].frame_index == 1
    assert frames[0].cycle == 0
    assert frames[0].t_ms == 20
    assert frames[0].strips == [b'LGB', b'RGB']   # manifest order, not arrival
    assert a.drain() == []


def test_index_is_floor_of_time_times_fps():
    a = PreviewAssembler()
    a.set_program([('main', 1)], target_fps=50)
    # Render jitter lands t_ms just past the frame deadline; floor holds.
    a.add('main', 0, 41, RGB)
    assert a.drain()[0].frame_index == 2


def test_index_is_exact_on_frame_boundaries():
    # Integer ms: 60 ms at 50 fps is index 3 exactly, with no float noise to
    # push it down to 2; one ms earlier is still index 2.
    a = PreviewAssembler()
    a.set_program([('main', 1)], target_fps=50)
    a.add('main', 0, 60, RGB)
    a.add('main', 0, 59, RGB)
    assert sorted(f.frame_index for f in a.drain()) == [2, 3]


def test_frame_time_is_its_slot_start():
    a = PreviewAssembler()
    a.set_program([('main', 1)], target_fps=30)
    a.add('main', 0, 70, RGB)              # index 2 at 30 fps: slot [66.7, 100)
    frame = a.drain()[0]
    assert (frame.frame_index, frame.t_ms) == (2, 66)


def test_wrong_length_rgb_dropped():
    a = PreviewAssembler()
    a.set_program([('main', 1)], target_fps=50)   # expects 3 bytes
    a.add('main', 0, 20, b'\x00\x00')              # 2 bytes
    assert a.drain() == []
    a.add('main', 0, 20, RGB)
    assert len(a.drain()) == 1


def test_unknown_strip_ignored():
    a = PreviewAssembler()
    a.set_program([('main', 1)], target_fps=50)
    a.add('ghost', 0, 20, RGB)
    a.add('main', 0, 20, RGB)
    assert [f.strips for f in a.drain()] == [[RGB]]


def test_mirrored_strip_first_frame_wins():
    a = PreviewAssembler()
    a.set_program([('wall', 1)], target_fps=50)
    a.add('wall', 0, 20, b'aaa')
    a.add('wall', 0, 20, b'bbb')           # same bucket, already emitted: ignored
    assert [f.strips for f in a.drain()] == [[b'aaa']]


def test_no_program_or_no_fps_ignores():
    a = PreviewAssembler()
    a.add('main', 0, 20, RGB)              # set_program never called
    assert a.drain() == []


def test_stale_future_frame_does_not_block_real_frames():
    # A stale end-of-previous-run frame (high index) must not poison the low
    # indices of the current run; at worst it flashes one spurious frame.
    a = PreviewAssembler()
    a.set_program([('main', 1)], target_fps=50)
    a.add('main', 0, 2000, RGB)            # index 100, emits once
    a.add('main', 0, 20, RGB)              # index 1, still emits
    assert sorted(f.frame_index for f in a.drain()) == [1, 100]


def test_reset_run_clears_partials_and_emitted():
    a = PreviewAssembler()
    a.set_program([('left', 1), ('right', 1)], target_fps=50)
    a.add('left', 0, 20, RGB)              # partial
    a.reset_run()
    a.add('right', 0, 20, RGB)
    assert a.drain() == []                 # the old 'left' is gone


def test_overflow_caps_pending_buckets():
    a = PreviewAssembler()
    a.set_program([('left', 1), ('right', 1)], target_fps=50)
    for i in range(_MAX_PENDING_BUCKETS + 3):
        a.add('left', 0, i * 20, RGB)      # all partial, never complete
    assert len(a._buckets) <= _MAX_PENDING_BUCKETS


# ---------------------------------------------------------------------------
# Looping programs: the same position recurs every cycle
# ---------------------------------------------------------------------------

def test_same_position_in_a_later_cycle_is_a_new_frame():
    a = PreviewAssembler()
    a.set_program([('main', 1)], target_fps=50)
    a.add('main', 0, 20, b'c0!')
    a.add('main', 1, 20, b'c1!')           # not de-duplicated against cycle 0
    frames = a.drain()
    assert [(f.cycle, f.frame_index, f.strips) for f in frames] == [
        (0, 1, [b'c0!']), (1, 1, [b'c1!'])]


def test_late_packet_from_previous_cycle_does_not_join_current_bucket():
    a = PreviewAssembler()
    a.set_program([('left', 1), ('right', 1)], target_fps=50)
    a.add('left', 1, 20, b'L1!')           # cycle 1, waiting on 'right'
    a.add('right', 0, 20, b'R0!')          # delayed packet from cycle 0
    assert a.drain() == []                 # no mixed-cycle frame
    a.add('right', 1, 20, b'R1!')
    frames = a.drain()
    assert [(f.cycle, f.strips) for f in frames] == [(1, [b'L1!', b'R1!'])]


def test_late_duplicate_from_previous_cycle_is_dropped():
    a = PreviewAssembler()
    a.set_program([('main', 1)], target_fps=50)
    a.add('main', 0, 980, RGB)
    a.add('main', 1, 0, RGB)
    assert len(a.drain()) == 2
    a.add('main', 0, 980, RGB)             # a straggler for an emitted frame
    assert a.drain() == []


def test_stale_partials_from_earlier_cycles_are_evicted_first():
    a = PreviewAssembler()
    a.set_program([('left', 1), ('right', 1)], target_fps=50)
    a.add('left', 0, 900, RGB)             # cycle 0 partial that never completes
    for i in range(_MAX_PENDING_BUCKETS):
        a.add('left', 1, i * 20, RGB)      # cycle 1 partials fill the window
    assert len(a._buckets) == _MAX_PENDING_BUCKETS
    assert all(cycle == 1 for cycle, _ in a._buckets)
