BEAT = 1.0
DURATION = 16.0

from elements.dsl import PI, sec, strip, wave

main = strip("ring35")
n = main.length
all_pixels = main.pixels(f"0-{n - 1}")

red_alert = wave(
    channel="V",
    h=0,
    s=1.0,
    v=0.0,
    min_val=0.0,
    max_val=1.0,
    period=sec(1.0),
    phase0=PI / 2,
    pixel_step=0.0,
)

red_alert.schedule(all_pixels, at=0, duration=sec(DURATION))
