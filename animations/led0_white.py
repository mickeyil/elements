BEAT = 1.0
DURATION = 60.0

from elements.dsl import sec, strip, paint

main = strip("test50")
led0 = main.pixels("0")

pixel0 = paint(color="white")
pixel0.schedule(led0, at=0, duration=sec(DURATION))
