from __future__ import annotations

import ctypes
import os

_WINDOWS_JOB_HANDLE: int | None = None
_KERNEL32: ctypes.WinDLL | None = None


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kernel32() -> ctypes.WinDLL | None:
    global _KERNEL32
    if os.name != "nt":
        return None
    if _KERNEL32 is not None:
        return _KERNEL32
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    _KERNEL32 = kernel32
    return kernel32


def install_kill_on_close_job() -> bool:
    """Put the server in a Windows Job that kills worker descendants on exit."""
    global _WINDOWS_JOB_HANDLE
    if os.name != "nt" or _WINDOWS_JOB_HANDLE is not None:
        return bool(_WINDOWS_JOB_HANDLE)
    kernel32 = _kernel32()
    if kernel32 is None:
        return False
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return False
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    if not ok:
        kernel32.CloseHandle(job)
        return False
    ok = kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess())
    if not ok:
        kernel32.CloseHandle(job)
        return False
    _WINDOWS_JOB_HANDLE = int(job)
    return True


def assign_process_to_job(pid: int) -> bool:
    """Explicitly attach a worker process to the server Job Object."""
    if os.name != "nt" or not _WINDOWS_JOB_HANDLE:
        return False
    kernel32 = _kernel32()
    if kernel32 is None:
        return False
    process = kernel32.OpenProcess(0x0200 | 0x0400, False, int(pid))  # SET_QUOTA | TERMINATE
    if not process:
        return False
    try:
        return bool(kernel32.AssignProcessToJobObject(ctypes.c_void_p(_WINDOWS_JOB_HANDLE), process))
    finally:
        kernel32.CloseHandle(process)
