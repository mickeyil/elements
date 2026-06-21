BEAT = 1.0
DURATION = 8.5

from elements.dsl import PI, sec, spark, strip, wave

main = strip("ring16")
n = main.length
all_pixels = main.pixels(f"0-{n - 1}")

ROUND_SECONDS = 1.0
ROUND_COUNT = 8
WAVE_PERIOD_SECONDS = ROUND_SECONDS * 4.0
RUNNER_STEP_SECONDS = ROUND_SECONDS / n
RUNNER_FADE_SECONDS = 0.5
RUNNER_VALUE = 0.5
MAX_V = 0.35
HUES = (0, 30, 60, 120, 180, 220, 270, 315)

pixel_step = 0.0 if n <= 1 else -(2 * PI / n)

for i, hue in enumerate(HUES):
    at_seconds = i * ROUND_SECONDS
    phase0 = PI / 2 + (2 * PI * at_seconds / WAVE_PERIOD_SECONDS)
    background = wave(
        channel="V",
        h=hue,
        s=1.0,
        v=0.0,
        min_val=0.04,
        max_val=MAX_V,
        period=sec(WAVE_PERIOD_SECONDS),
        phase0=phase0,
        pixel_step=pixel_step,
    )
    background.schedule(
        all_pixels,
        at=sec(at_seconds),
        duration=sec(ROUND_SECONDS),
    )

tail_start_seconds = ROUND_COUNT * ROUND_SECONDS
tail_phase0 = PI / 2 + (2 * PI * tail_start_seconds / WAVE_PERIOD_SECONDS)
tail_background = wave(
    channel="V",
    h=HUES[-1],
    s=1.0,
    v=0.0,
    min_val=0.04,
    max_val=MAX_V,
    period=sec(WAVE_PERIOD_SECONDS),
    phase0=tail_phase0,
    pixel_step=pixel_step,
)
tail_background.schedule(
    all_pixels,
    at=sec(tail_start_seconds),
    duration=sec(RUNNER_FADE_SECONDS),
)

for i in range(ROUND_COUNT * n):
    pixel_index = i % n
    at_seconds = i * RUNNER_STEP_SECONDS
    runner = spark(color=(0.0, 0.0, RUNNER_VALUE), fade=sec(RUNNER_FADE_SECONDS))
    runner.schedule(
        main.pixels(str(pixel_index)),
        at=sec(at_seconds),
        duration=sec(RUNNER_FADE_SECONDS),
    )
