BEAT = 1.0
DURATION = 4.0

from elements.dsl import PI, sec, strip, wave

main = strip("ring16")
n = main.length
pixel_step = 0.0 if n <= 1 else -(2 * PI / (n - 1))

blue_wave = wave(
    channel="V",
    h=220,
    s=1.0,
    v=0.0,
    min_val=0.05,
    max_val=0.80,
    period=4.0,
    phase0=0.0,
    pixel_step=pixel_step,
)

blue_wave.schedule(main.pixels(f"0-{n - 1}"), at=0, duration=sec(DURATION))
