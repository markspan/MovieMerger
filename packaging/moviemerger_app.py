"""Entry point that Nuitka compiles into the MovieMerger executable.

One executable is both the desktop app (started without arguments, e.g. by
double-clicking) and the command-line tool (started with arguments), exactly
like ``python -m moviemerger``. See ``packaging/build.py`` for the build.
"""

import sys

from moviemerger.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
