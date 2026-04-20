# 1. Colors + Gamma

Combines roadmap steps 1 and 2. Moves gamma out of `colors.*` and lands
the new `GammaCorrection` module in the same round, shrinking the
migration window in which the tree depends on a missing symbol.

## `src/colors.h` / `src/colors.cpp`

- Delete `gamma_correct` decl + body and the `gamma_table[256]` in the
  `.cpp`.
- Add `#include <type_traits>` and the four asserts from
  `drafts/colors.h:29-36` (`sizeof`, `alignof`, `is_standard_layout`,
  `is_trivially_copyable` on `hsva_t`) at namespace scope in the
  header. These pin the layout that `PixelBufferPool` assumes.
- Keep `hsva_t`, `rgb_t`, `hsv_to_rgb`, `rgb_alpha_blend` unchanged. Only
  touch `hsva_t`'s constructors if `is_trivially_copyable` actually
  fails.

## New `src/gamma.{h,cpp}`

Port `drafts/gamma.{h,cpp}` verbatim. Shape:

- `GammaCorrection` owns a `uint8_t _lut[256]` + last accepted
  `_gamma`. Default construction = identity (direct `i` assignment, no
  `powf`).
- `set_gamma(float)` accepts exactly `1.0` (identity) and
  `(1.0, MAX_SUPPORTED_GAMMA]` (generates `powf(x, gamma)` LUT).
  Anything else is rejected and leaves the LUT unchanged.
- `correct(rgb_t)` does three LUT lookups; `gamma()` returns the last
  accepted value.
- Constants use the new SCREAMING_SNAKE style (see
  `drafts/runtime_constants.h`): `IDENTITY_GAMMA = 1.0f`,
  `MAX_SUPPORTED_GAMMA = 5.0f`, `DEFAULT_GAMMA = 2.8f` (recommended
  for WS2812 in dark rooms). The `drafts/gamma.{h,cpp}` sources use
  the old `k*` names; rename on the way in.

No `apply_gamma(Strip&, …)` yet — implementing it requires the v3
`Strip` from step 13. `drafts/roadmap.md:45` and `:117` currently
place `apply_gamma` in step 2; update that wording to show step 13
introducing `apply_gamma` together with the v3 `Strip`.

## Tests

- `test/test_colors.cpp` — delete the four `gamma_correct` cases
  (lines 82-108). hsv + lerp cases stay. Static asserts are
  compile-time; no runtime test needed.
- New `test/test_gamma.cpp` (wire into `CMakeLists.txt` next to
  `test_colors`). Cover: identity passthrough for `1.0` and default
  ctor; `2.8` preserves 0 and 255, darkens midpoint, and matches a
  golden LUT value at one known index (catches wrong-exponent
  regressions without pinning the whole table); accepted boundary at
  exactly `MAX_SUPPORTED_GAMMA`; rejected values (`0.0`, negative,
  `NaN`, `> MAX_SUPPORTED_GAMMA`) leave the prior LUT and `gamma()`
  unchanged; `set_gamma(2.8f)` followed by `set_identity()` returns to
  passthrough.

## Known breakage — deferred to step 13

`src/compositor.cpp:36` and `test/test_compositor.cpp` still reference
`gamma_correct` and will fail to compile against the new `colors.*`.
Per `drafts/roadmap.md:10-15` this is intentional; they get rewired
onto `apply_gamma(Strip&, const GammaCorrection&)` in step 13 when the
v3 `Strip` lands. Do not patch compositor in this round.

## Keep module-level tests buildable

`elements_core` currently pulls in `compositor.cpp`, which stops
compiling once `gamma_correct` is gone, so the library archive is
never produced and every test binary wired to it fails to build.
Adjust `CMakeLists.txt` so `test_colors` and `test_gamma` do not
depend on `elements_core`:

- build each as a standalone executable from its own source + the
  module source(s) it exercises: `test_colors` = `test_colors.cpp` +
  `src/colors.cpp`; `test_gamma` = `test_gamma.cpp` + `src/gamma.cpp`
  (no `src/colors.cpp` — `GammaCorrection` only uses `rgb_t`, whose
  constructors are inline in `colors.h`).
- link only `Catch2::Catch2WithMain` (no `elements_core`).

Other tests (`test_decoder`, `test_engine`, `test_compositor`, …) stay
wired to `elements_core` and will fail to build until step 13. That is
acceptable; colors/gamma coverage runs in isolation during the window.

## Verification

A plain `cmake --build build` still tries `elements_core` and the
other test targets and will fail. Build and run the two module
targets explicitly:

```
cmake -B build
cmake --build build --target test_colors test_gamma
./build/test_colors
./build/test_gamma
```

- Both targets build and pass.
- `grep -rn gamma_correct src/ test/` returns only the two expected
  step-13 sites (`src/compositor.cpp`, `test/test_compositor.cpp`).
- The layout asserts are verified by successful compilation of any TU
  that includes `colors.h`; no runtime or negative-edit check.
