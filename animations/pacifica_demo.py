BEAT = 1.0
DURATION = 60.0

from elements.dsl import sec, strip, pacifica

main = strip("main")
n = main.length

ocean = pacifica(
    speed=1.0,
    brightness=1.0,
    hue_shift=0.0,
)

ocean.schedule(main.pixels(f"0-{n - 1}"), at=0, duration=sec(DURATION))
