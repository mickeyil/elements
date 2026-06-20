"""`python -m elemctl` runs the controller server.

The launcher script dispatches subcommands (sim, web, ...) directly to
their modules; the bare module entry point is the server.
"""

from .server import main

main()
