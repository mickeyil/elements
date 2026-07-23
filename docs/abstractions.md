# Abstractions

Core runtime abstractions in the firmware, and what each is for.

## PixelBufferPool

`src/core/pixel_buffer_pool.h`, `src/core/pixel_buffer_pool.cpp`

A *pool buffer* is a block of working pixel memory the program draws into.
Each buffer is an array of f32 HSVA pixels. Animations render their output
into buffers, and copy ops shuffle pixels between them; the final frame is
composited out of these buffers and pushed to the strip.

Buffers are accessed through pixel views. The compiler decides how many
buffers a program needs and how big each one is; the device allocates all
pixel memory once, as a single bounded block, at program-load time.

## PixelView

`src/core/pixel_view.h`, `src/core/pixel_view.cpp`

A pool buffer is a row of hsva pixels; each is a hue, saturation, value,
and alpha. An animation paints colors into them; they are the working
memory a frame is built from.

```
  [ hsva ][ hsva ][ hsva ][ hsva ]
```

An animation thinks in its own coordinate space (logical pixel 0, 1, 2,
…) and reaches the buffer through a pixel view. The view maps each
logical pixel to an hsva pixel in the buffer (its *storage*), and, for
views the compositor reads, to an LED on the strip (its *physical*
mapping). The animation stays in logical space; the view carries the rest.

A view is bound once:

```cpp
uint16_t storage[3]  = {1, 2, 3};   // logical pixel i  ->  buffer pixel storage[i]
uint16_t physical[3] = {5, 6, 7};   // logical pixel i  ->  strip LED  physical[i]

v.initialize(buf, /*size=*/3, storage,
             /*has_physical=*/true, physical, /*physical_identity=*/false);
```

The animation renders by writing logical pixels; each write lands in the
buffer pixel it maps to:

```cpp
for (uint16_t i = 0; i < v.size(); ++i)
    v[i] = hsva_t(hue, 1, 1, 1);
```

```
            v[0]    v[1]    v[2]      logical pixels the animation writes
              |       |       |
              v       v       v
  [ hsva ][ hsva ][ hsva ][ hsva ]    buffer of hsva pixels
```

Then the compositor reads the view's logical pixels, asks each one which
LED it drives, converts hsva to rgb, and writes the strip:

```cpp
for (uint16_t i = 0; i < view->size(); ++i) {
    const hsva_t& px  = (*view)[i];
    const uint16_t led = view->physical_index(i);
    out[led] = hsv_to_rgb(px.h, px.s, px.v);
}
```

```
                           v[0] v[1] v[2]    logical pixels
                             |    |    |
                             v    v    v
  [   ][   ][   ][   ][   ][rgb][rgb][rgb]    strip LEDs
```

So one `v[i]` threads three spaces: the logical pixel the animation
writes, the buffer pixel that holds it, and the strip LED that shows it. A
mapping left out is identity: logical pixel `i` is buffer pixel `i`, or
strip LED `i`.
