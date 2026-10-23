"""``python -m moviemerger``: the GUI without arguments, the CLI with them.

The compiled releases use the same entry point, so one executable serves as
both the desktop app and the command-line tool.
"""

import sys


def main() -> int:
    """Start the GUI when run without arguments, otherwise the CLI."""
    if len(sys.argv) > 1:
        from .cli import main as cli_main

        return cli_main()
    from .gui import main as gui_main

    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
