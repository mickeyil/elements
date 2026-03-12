BEAT = 0.5
DURATION = 4.0

from elements.dsl import sec, spark, strip

main = strip("main", length=60)
twinkle = spark(color="white", fade=1.0)
twinkle.schedule(main.pixels("0-59"), at=0, duration=sec(DURATION))
