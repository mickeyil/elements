"""Estimate the esp32 RAM footprint of a compiled blob.

The decoder turns a blob into device structures (pool, pixel views, copy ops,
layers/events, animation instances). This sums their sizes so the operator can
see a program's memory cost. Allocator overhead and fragmentation are ignored;
the estimate guides optimization, not exact budgeting.

The byte constants are the real esp32 (32-bit Xtensa) sizeof of each struct,
NOT the host sizeof; the compiler runs on a 64-bit host, so host sizes would be
wrong. test_memory_estimate.py compiles a probe with the esp32 toolchain and
asserts these stay in sync.
"""

from __future__ import annotations

from .types import MemoryEstimate
from .blob_v3 import BlobProgram, ANIM_WAVE, ANIM_SHIFT, ANIM_SPARK, ANIM_PAINT

# esp32 sizeof, verified against the xtensa toolchain.
_HSVA_BYTES = 16          # hsva_t (4 floats); also the pool element size
_PIXEL_VIEW_BYTES = 16    # PixelView record (buffer + 2 index ptrs + size + flags)
_COPY_OP_BYTES = 8        # CopyOp (f32 at + 2 u16 view indices)
_ANIM_EVENT_BYTES = 20    # AnimationEvent (Animation* + 2 f32 + 3 u16)
_LAYER_BYTES = 8          # Layer (AnimationEvent* + count)
_PROGRAM_BYTES = 44       # Program shell (owns pool/views/copy-ops by value)
_PTR_BYTES = 4            # pointer width on the 32-bit target
_U16_BYTES = 2            # one entry of an owned storage/physical index array

# Per-event Animation instance, by anim type (vtable pointer included).
_ANIM_INSTANCE_BYTES = {
    ANIM_WAVE: 40,
    ANIM_SHIFT: 36,
    ANIM_SPARK: 20,
    ANIM_PAINT: 32,   # plus count * _HSVA_BYTES for the owned constant array
}

# Per-pixel paint bakes a constant hsva_t array into the instance.
_PAINT_CONSTANT_MODE = 1


def estimate_memory(program: BlobProgram) -> MemoryEstimate:
    """Estimate the device RAM a decoded program occupies."""
    # Pixel storage plus the pool's per-buffer pointer and size tables.
    pool_bytes = (sum(program.buffer_sizes) * _HSVA_BYTES
                  + len(program.buffer_sizes) * (_PTR_BYTES + _U16_BYTES))

    view_bytes = 0
    for v in program.pixel_views:
        view_bytes += _PIXEL_VIEW_BYTES
        if not v.storage_identity:
            view_bytes += v.size * _U16_BYTES
        if v.has_physical and not v.physical_identity:
            view_bytes += v.size * _U16_BYTES

    copy_op_bytes = len(program.copy_ops) * _COPY_OP_BYTES

    event_count = sum(len(layer.events) for layer in program.layers)
    event_bytes = event_count * _ANIM_EVENT_BYTES + len(program.layers) * _LAYER_BYTES

    anim_bytes = 0
    for layer in program.layers:
        for e in layer.events:
            anim_bytes += _ANIM_INSTANCE_BYTES.get(e.anim_type, 0)
            if (e.anim_type == ANIM_PAINT and len(e.params) >= 3
                    and e.params[0] == _PAINT_CONSTANT_MODE):
                count = int.from_bytes(e.params[1:3], "little")  # u16
                anim_bytes += count * _HSVA_BYTES

    overhead_bytes = _PROGRAM_BYTES
    total_bytes = (pool_bytes + view_bytes + copy_op_bytes + event_bytes
                   + anim_bytes + overhead_bytes)
    return MemoryEstimate(
        pool_bytes=pool_bytes,
        view_bytes=view_bytes,
        copy_op_bytes=copy_op_bytes,
        event_bytes=event_bytes,
        anim_bytes=anim_bytes,
        overhead_bytes=overhead_bytes,
        total_bytes=total_bytes,
    )


def format_report(per_strip: dict[str, MemoryEstimate]) -> str:
    """Render per-strip footprints and the peak device, for CLI/debug output."""
    if not per_strip:
        return "[elements] memory estimate: no strips"
    lines = ["[elements] firmware memory estimate (per-strip, esp32, "
             "allocator overhead ignored):"]
    peak_strip, peak = "", -1
    for sid, m in per_strip.items():
        lines.append(
            f"  {sid} : pool {m.pool_bytes} B  views {m.view_bytes} B  "
            f"copy {m.copy_op_bytes} B  events {m.event_bytes} B  "
            f"anims {m.anim_bytes} B  overhead {m.overhead_bytes} B  "
            f"= {m.total_bytes} B"
        )
        if m.total_bytes > peak:
            peak_strip, peak = sid, m.total_bytes
    lines.append(f"  peak : {peak_strip} {peak} B")
    return "\n".join(lines)
