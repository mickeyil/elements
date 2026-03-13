"""Compatibility shim for the old elemctl.serve module name."""

from .server import UdsServer, main


if __name__ == '__main__':
    main()
