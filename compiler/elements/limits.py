"""Structural caps mirrored from the C++ decoder.

These bound the binary blob the compiler emits; the decoder rejects any
blob that exceeds them (src/blob_limits.h, plus MAX_STRIP_PIXELS from
src/hardware_profile.h). The compiler rejects the same programs at
compile time, where the error is far easier to act on than a firmware
rejection. test_blob_limits.py parses the C++ headers and asserts these
stay equal.
"""

from __future__ import annotations

MAX_LAYER_COUNT = 32
MAX_STRIP_PIXELS = 300
MAX_BUFFER_COUNT = 256
MAX_PIXEL_VIEW_COUNT = 512
MAX_COPY_OP_COUNT = 512
MAX_EVENTS_PER_LAYER = 1024
MAX_EVENT_PARAMS_BYTES = 8192
MAX_POOL_BYTES = 100 * 1024
