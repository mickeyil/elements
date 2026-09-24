from elements.dsl import forever, strip, pacifica

BEAT = 1.0
DURATION = forever

main = strip("s50-1")
n = main.length

ocean = pacifica(
    speed=1.0,
    brightness=1.0,
    hue_shift=0.0,
)

ocean.schedule(main.pixels(f"0-{n - 1}"), at=0, duration=forever)
