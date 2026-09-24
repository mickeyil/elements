"""Preview-frame assembly: per-strip device previews into program frames.

Sims send one RGB preview per strip each rendered frame (wire.FramePreview),
carrying the frame's place on the program timeline: the loop cycle and whole
ms into it. The session owns an assembler that derives a program-frame index
from that time and the program's constant target_fps, buckets strips by
(cycle, index), and emits one ProgramFrame once every strip in the loaded
program has reported a given bucket.

Keying on time, not the sim's own frame_index, is deliberate: that counter is
per-device process-lifetime state, so it cannot align strips across devices.
Two synced devices compute nearly the same t_ms for the same frame, so
t_ms * fps // 1000 lands them in the same bucket; integer ms make that exact.
The cycle keeps a late packet from the previous loop pass out of the current
pass's buckets. This is a preview approximation, not proof of physical sync:
render jitter near a bucket boundary, a stalled device, or an unsynced
program can drop or misplace a frame; the preview tolerates that, the
protocol does not depend on it.
"""

from dataclasses import dataclass

# Pending buckets kept for frames that never complete (a strip dropped its
# packet); the oldest are evicted so a missing strip cannot grow without bound.
# Keys order by (cycle, index), so buckets from earlier cycles go first.
_MAX_PENDING_BUCKETS = 8

# Recently emitted buckets remembered for de-duplication (a mirrored strip
# reporting an already-completed frame, or a late straggler).
_MAX_EMITTED = 256


@dataclass(frozen=True)
class ProgramFrame:
    frame_index: int   # time-derived index within the cycle, not the sim counter
    cycle: int         # loop cycle; 0 unless the program loops
    t_ms: int          # start of the frame's slot within the cycle, whole ms
    strips: list       # one rgb bytes blob per strip, in manifest order


class PreviewAssembler:
    """Buckets per-strip previews into whole-program frames, keyed by the
    loop cycle and a time-derived frame index.

    set_program() declares the strips to expect, their order in an assembled
    frame, and the program's fps; it also starts a fresh run. reset_run()
    starts a fresh run on the same program (a replay). add() files one strip's
    frame; a bucket completes once every expected strip has reported it.
    drain() returns the frames completed since the last call.
    """

    def __init__(self):
        self._order = []        # strip_ids in manifest order
        self._expected = set()  # same, for membership tests
        self._byte_len = {}     # strip_id -> expected rgb byte length
        self._fps = 0
        self._buckets = {}      # (cycle, index) -> {strip_id: rgb}
        self._ready = []        # completed ProgramFrames awaiting drain
        self._emitted = set()   # recently emitted (cycle, index) keys (de-dup)
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

    def add(self, strip_id, cycle, t_ms, rgb):
        if strip_id not in self._expected or self._fps <= 0:
            return
        if len(rgb) != self._byte_len[strip_id]:
            return   # wrong-profile or corrupt packet: undecodable in a frame
        key = (cycle, t_ms * self._fps // 1000)
        if key in self._emitted:
            return
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = {}
            self._buckets[key] = bucket
            self._evict_overflow()
        if strip_id in bucket:
            return   # a mirrored strip already reported this frame; first wins
        bucket[strip_id] = rgb
        if len(bucket) == len(self._expected):
            self._complete(key, bucket)

    def drain(self):
        ready, self._ready = self._ready, []
        return ready

    def _complete(self, key, bucket):
        cycle, index = key
        strips = [bucket[sid] for sid in self._order]
        self._ready.append(
            ProgramFrame(index, cycle, index * 1000 // self._fps, strips))
        del self._buckets[key]
        self._emitted.add(key)
        self._emitted_order.append(key)
        if len(self._emitted_order) > _MAX_EMITTED:
            self._emitted.discard(self._emitted_order.pop(0))

    def _evict_overflow(self):
        while len(self._buckets) > _MAX_PENDING_BUCKETS:
            del self._buckets[min(self._buckets)]
