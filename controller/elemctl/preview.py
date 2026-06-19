"""Preview-frame assembly: per-strip device previews into program frames.

Sims send one RGB preview per strip each rendered frame (wire.FramePreview),
carrying the frame's program time. The session owns an assembler that derives a
program-frame index from that time and the program's constant target_fps,
buckets strips by it, and emits one ProgramFrame once every strip in the loaded
program has reported a given index.

Keying on time, not the sim's own frame_index, is deliberate: that counter is
per-device process-lifetime state, so it cannot align strips across devices.
Two synced devices compute nearly the same t_program for the same frame, so
floor(t_program * fps) lands them in the same bucket. This is a preview
approximation, not proof of physical sync: render jitter near a bucket boundary,
a stalled device, or an unsynced program can drop or misplace a frame; the
preview tolerates that, the protocol does not depend on it.
"""

import math
from dataclasses import dataclass

# Pending buckets kept for frames that never complete (a strip dropped its
# packet); the oldest are evicted so a missing strip cannot grow without bound.
_MAX_PENDING_BUCKETS = 8

# Recently emitted indices remembered for de-duplication (a mirrored strip
# reporting an already-completed frame, or a late straggler).
_MAX_EMITTED = 256

# Absorbs float noise at a bucket boundary only, never real scheduling drift.
_INDEX_EPSILON = 1e-6


@dataclass(frozen=True)
class ProgramFrame:
    frame_index: int   # program-frame index (time-derived), not the sim counter
    t_rel: float       # program time of the frame, seconds
    strips: list       # one rgb bytes blob per strip, in manifest order


class PreviewAssembler:
    """Buckets per-strip previews into whole-program frames, keyed by a
    time-derived frame index.

    set_program() declares the strips to expect, their order in an assembled
    frame, and the program's fps; it also starts a fresh run. reset_run()
    starts a fresh run on the same program (a replay). add() files one strip's
    frame; a bucket completes once every expected strip has reported its index.
    drain() returns the frames completed since the last call.
    """

    def __init__(self):
        self._order = []        # strip_ids in manifest order
        self._expected = set()  # same, for membership tests
        self._byte_len = {}     # strip_id -> expected rgb byte length
        self._fps = 0
        self._buckets = {}      # index -> {strip_id: rgb}
        self._ready = []        # completed ProgramFrames awaiting drain
        self._emitted = set()   # recently emitted indices (de-dup)
        self._emitted_order = []

    def set_program(self, strips, target_fps):
        """strips: (strip_id, pixel_length) entries in manifest order."""
        self._order = [sid for sid, _ in strips]
        self._expected = set(self._order)
        self._byte_len = {sid: length * 3 for sid, length in strips}
        self._fps = target_fps
        self.reset_run()

    def reset_run(self):
        self._buckets = {}
        self._ready = []
        self._emitted = set()
        self._emitted_order = []

    def add(self, strip_id, t_program, rgb):
        if strip_id not in self._expected or self._fps <= 0:
            return
        if len(rgb) != self._byte_len[strip_id]:
            return   # wrong-profile or corrupt packet: undecodable in a frame
        index = math.floor(t_program * self._fps + _INDEX_EPSILON)
        if index < 0 or index in self._emitted:
            return
        bucket = self._buckets.get(index)
        if bucket is None:
            bucket = {}
            self._buckets[index] = bucket
            self._evict_overflow()
        if strip_id in bucket:
            return   # a mirrored strip already reported this frame; first wins
        bucket[strip_id] = rgb
        if len(bucket) == len(self._expected):
            self._complete(index, bucket)

    def drain(self):
        ready, self._ready = self._ready, []
        return ready

    def _complete(self, index, bucket):
        strips = [bucket[sid] for sid in self._order]
        self._ready.append(ProgramFrame(index, index / self._fps, strips))
        del self._buckets[index]
        self._emitted.add(index)
        self._emitted_order.append(index)
        if len(self._emitted_order) > _MAX_EMITTED:
            self._emitted.discard(self._emitted_order.pop(0))

    def _evict_overflow(self):
        while len(self._buckets) > _MAX_PENDING_BUCKETS:
            del self._buckets[min(self._buckets)]
