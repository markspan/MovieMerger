"""Windows: give a process started without a console usable standard handles.

When the compiled app is double-clicked (or a file is dropped on it), Windows
starts it without a console, and its standard input/output/error handles
can be invalid rather than absent. ``subprocess`` hands a process's standard
handles to every child it starts, and duplicating an invalid one fails with
``OSError: [WinError 6] The handle is invalid``. That broke every ffmpeg
call, including the check ``imageio-ffmpeg`` runs before it reports its
bundled ffmpeg, so ffmpeg then looked missing.

:func:`repair_standard_handles` points each unusable standard handle at the
``NUL`` device, so children inherit valid handles whatever the libraries
starting them pass to ``subprocess``.
"""

from __future__ import annotations

import os
import sys
from typing import IO

# Standard handle ids (STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE) as
# the DWORDs the API expects, with the mode to open NUL in for each.
_STANDARD_HANDLES = ((-10 & 0xFFFFFFFF, "rb"), (-11 & 0xFFFFFFFF, "wb"), (-12 & 0xFFFFFFFF, "wb"))
_FILE_TYPE_UNKNOWN = 0
_NO_ERROR = 0

# The NUL files backing the replaced handles; closing one would invalidate it again.
_nul_files: list[IO[bytes]] = []


def repair_standard_handles() -> None:
    """Point every unusable standard handle of this process at ``NUL`` (Windows only).

    Usable handles, such as a terminal's or a redirection's, are left alone,
    so command-line output still appears where it should. A no-op elsewhere.
    """
    if sys.platform != "win32":
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.GetFileType.argtypes = [wintypes.HANDLE]
    kernel32.GetFileType.restype = wintypes.DWORD
    kernel32.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
    kernel32.SetStdHandle.restype = wintypes.BOOL
    invalid_handle_value = ctypes.c_void_p(-1).value

    def usable(handle: int | None) -> bool:
        if handle in (None, 0, invalid_handle_value):
            return False
        ctypes.set_last_error(_NO_ERROR)
        # GetFileType reports "unknown" for some valid handles too; only an
        # accompanying error marks the handle as invalid.
        return kernel32.GetFileType(handle) != _FILE_TYPE_UNKNOWN or ctypes.get_last_error() == _NO_ERROR

    for std_id, mode in _STANDARD_HANDLES:
        if usable(kernel32.GetStdHandle(std_id)):
            continue
        nul = open(os.devnull, mode)  # noqa: SIM115 (must stay open for the life of the process)
        _nul_files.append(nul)
        if not kernel32.SetStdHandle(std_id, msvcrt.get_osfhandle(nul.fileno())):
            raise ctypes.WinError(ctypes.get_last_error())
