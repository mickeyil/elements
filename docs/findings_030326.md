# Findings: 2026-03-03

## 1) Multi-strip composition is not represented in layering/blobs
**Severity:** High  
**Status:** Remaining  

### Evidence
- `PixelGroup` carries `strip_name`, but layer packing ignores it:
  - [compiler/elements/types.py](compiler/elements/types.py:99)
  - [compiler/elements/compiler.py](compiler/elements/compiler.py:175)
- Blob format has no strip identifier for layers/events:
  - [compiler/elements/blob.py](compiler/elements/blob.py:5-43, 118-151)
- Decoder follows that same layout:
  - [compiler/elements/blob.py](compiler/elements/blob.py:160-220)

### Impact
Events from different strips can be merged by matching numeric indices only, which is incorrect if two strips contain the same index values.

### Proposed fix
1. Track strip identity in layer/event data during layer inference.
2. Include strip identity in serialized format and decode path.
3. Add compile and decode tests with overlapping local indices across two strips.

---

## 2) Snapshot source layer resolution is `AnimDef`-identity-based, not event-specific
**Severity:** Medium  
**Status:** Remaining  

### Evidence
- `_resolve_snapshot_layers` maps `id(e["anim"])` to a single layer:
  - [compiler/elements/compiler.py](compiler/elements/compiler.py:398-410)
- Snapshot params consume this single `_source_layer` value:
  - [compiler/elements/compiler.py](compiler/elements/compiler.py:368-381)

### Impact
Reusing the same `AnimDef` in multiple scheduled contexts can cause snapshot source-layer selection to point to the wrong layer when multiple source instances exist.

### Proposed fix
1. Resolve snapshot source against a specific source event occurrence (time and pixel coverage) rather than `AnimDef` identity.
2. Fail on ambiguous references instead of silently picking one.
3. Add regression tests for repeated animation objects used on multiple layers/schedules.

---

## 3) Numeric and geometry parameter validation is still incomplete
**Severity:** Medium  
**Status:** Remaining  

### Evidence
- Early validation currently only checks required keys:
  - [compiler/elements/compiler.py](compiler/elements/compiler.py:63-88)
- `_parse_indices` will accept malformed/reversed ranges without guarding and can produce empty/invalid groups:
  - [compiler/elements/types.py](compiler/elements/types.py:74-84)

### Impact
Invalid values are accepted by compiler and may produce nonsensical runtime behavior (e.g., out-of-range params, empty index groups).

### Proposed fix
1. Add range/type/finite checks for required params (`channel`, `direction`, `h/s/min/max/period/phase0/pixel_step`, `fade`, `velocity`, etc.).
2. Validate strip metadata (e.g., strip length > 0).
3. Harden index parsing to reject invalid tokens, reversed ranges, empty results, and non-integer indices.
4. Add tests that assert clear errors for malformed indices and out-of-range numeric values.

---

## 4) Shift velocity contract in docs is inconsistent with implementation
**Severity:** Low  
**Status:** Remaining  

### Evidence
- Docs list shift in time-parameter mapping:
  - [docs/compiler.md](docs/compiler.md:126-131)
- Implementation handles shift velocity separately:
  - [compiler/elements/compiler.py](compiler/elements/compiler.py:141-147)

### Impact
Current behavior for `shift(velocity=...)` with `sec()` is not clearly defined by docs, so API semantics are ambiguous.

### Proposed fix
1. Define one explicit contract for `shift.velocity` (beats unit or direct pixel/sec marker handling).
2. Update docs + compiler validation logic to exactly match that contract.
3. Add a test covering the documented use.

