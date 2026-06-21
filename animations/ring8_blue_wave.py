BEAT = 1.0
DURATION = 16.0

from elements.dsl import PI, sec, shift, strip, paint, wave

main = strip("ring8")
n = main.length
all_pixels = main.pixels(f"0-{n - 1}")

ROUND_SECONDS = 1.0
ROUND_COUNT = 8
RUNNER_START_SECONDS = ROUND_SECONDS * ROUND_COUNT
RUNNER_VELOCITY = n / ROUND_SECONDS
MAX_V = 0.40
HUES = (0, 30, 60, 120, 180, 220, 270, 315)

pixel_step = 0.0 if n <= 1 else -(2 * PI / n)
seeds = []

for i, hue in enumerate(HUES):
    at = sec(i * ROUND_SECONDS)

    seed_pixels = [(0.0, 0.0, 0.0, 0.0)] * n
    seed_pixels[0] = (hue, 1.0, MAX_V, 1.0)
    seed = paint(colors=seed_pixels)
    seed.schedule(all_pixels, at=at, duration=sec(ROUND_SECONDS))
    seeds.append(seed)

    fast_wave = wave(
        channel="V",
        h=hue,
        s=1.0,
        v=0.0,
        min_val=0.0,
        max_val=MAX_V,
        period=ROUND_SECONDS,
        phase0=PI / 2,
        pixel_step=pixel_step,
    )
    fast_wave.schedule(all_pixels, at=at, duration=sec(ROUND_SECONDS))

for i in range(ROUND_COUNT):
    runner = shift(
        direction="right",
        velocity=RUNNER_VELOCITY,
        circular=True,
        fill="transparent",
    )
    runner.schedule(
        all_pixels,
        at=sec(RUNNER_START_SECONDS + i * ROUND_SECONDS),
        duration=sec(ROUND_SECONDS),
        source=seeds[i],
    )
