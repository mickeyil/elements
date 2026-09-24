from elements.dsl import forever, strip, pacifica

BEAT = 1.0
DURATION = forever

# TARGET is the strip the operator panel runs this on, bound at compile time.
main = strip(TARGET)
n = main.length

ocean = pacifica(
    speed=1.0,
    brightness=1.0,
    hue_shift=0.0,
)

ocean.schedule(main.pixels(f"0-{n - 1}"), at=0, duration=forever)
