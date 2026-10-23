"""Start a Windows program the way Explorer starts a double-clicked GUI app.

Such a process has no console, and its standard handles can be invalid
rather than absent. ``subprocess`` cannot create that situation (it always
passes valid handles), so this module calls ``CreateProcessW`` directly with
deliberately unusable handles. Used by ``smoke_test.py`` to check that the
compiled app still runs ffmpeg when started this way.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes

if sys.platform != "win32":
    raise ImportError("consoleless is only available on Windows")

_STARTF_USESTDHANDLES = 0x00000100
_CREATE_NO_WINDOW = 0x08000000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_INFINITE = 0xFFFFFFFF

# A handle value no freshly started process owns: stands in for the stale,
# invalid handles a console-less GUI process can be given.
_UNUSABLE_HANDLE = 0x00FFFF00


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
    wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
    ctypes.POINTER(_StartupInfo), ctypes.POINTER(_ProcessInformation),
]  # fmt: skip
_kernel32.CreateProcessW.restype = wintypes.BOOL
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetExitCodeProcess.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


def run(command: list[str], env: dict[str, str]) -> int:
    """Run ``command`` without a console and with unusable standard handles.

    Args:
        command: Program and arguments.
        env: The child's complete environment.

    Returns:
        The child's exit code (its output cannot be captured: that is the point).
    """
    startup = _StartupInfo(
        cb=ctypes.sizeof(_StartupInfo),
        dwFlags=_STARTF_USESTDHANDLES,
        hStdInput=_UNUSABLE_HANDLE,
        hStdOutput=_UNUSABLE_HANDLE,
        hStdError=_UNUSABLE_HANDLE,
    )
    process = _ProcessInformation()
    # Windows expects the block sorted by name, case-insensitively.
    variables = sorted(env.items(), key=lambda item: item[0].upper())
    environment = ctypes.create_unicode_buffer("".join(f"{k}={v}\0" for k, v in variables) + "\0")
    command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))

    created = _kernel32.CreateProcessW(
        None, command_line, None, None, False, _CREATE_NO_WINDOW | _CREATE_UNICODE_ENVIRONMENT,
        environment, None, ctypes.byref(startup), ctypes.byref(process),
    )  # fmt: skip
    if not created:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        _kernel32.WaitForSingleObject(process.hProcess, _INFINITE)
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value
    finally:
        _kernel32.CloseHandle(process.hProcess)
        _kernel32.CloseHandle(process.hThread)
