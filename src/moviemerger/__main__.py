"""``python -m moviemerger``: the GUI without arguments, the CLI with them."""

import sys


def _run() -> int:
    if len(sys.argv) > 1:
        from .cli import main

        return main()
    from .gui import main

    return main()


sys.exit(_run())
