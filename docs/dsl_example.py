from elements.dsl import *

def program(beat, duration):
    strip1 = strip("main", length=10, type="RGB")
    all_pixels = strip1.pixels("0-9")
    left_group1 = strip1.pixels("0,4")
    right_group1 = strip1.pixels("5,9")

    blue_hue = 220

    wave1 = wave(
        channel="V",
        h=blue_hue, s=1.0,
        min_val=0.0, max_val=0.4,
        period=8,
        phase0=-PI/2,
        pixel_step=PI
    )

    shift1 = shift(
        direction="right",
        velocity=2,
        circular=False,
        fill="transparent"
    )

    spark_white = spark(color="white", fade=0.1)
    spark_yellow = spark(color="yellow", fade=0.1)

    wave1.schedule(all_pixels, at=0, duration=2)
    shift1.schedule(all_pixels, at=2, duration=2, source=wave1)

    for i in range(4):
        spark_white.schedule(left_group1, at=i, duration=sec(0.1))
        spark_yellow.schedule(right_group1, at=i + 0.5, duration=sec(0.1))

    return build(beat=beat, duration=duration)
