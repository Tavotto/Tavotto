"""Run a staged command inside a disposable, offline Windows AppContainer."""

from __future__ import annotations

import ctypes
import os
import subprocess
import uuid
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from ..runtime import CREATE_NO_WINDOW
from ..winprocess import EXTENDED_STARTUPINFO_PRESENT, ProcessStructures

_POINTER = ctypes.c_void_p
_DWORD = ctypes.c_uint32
_BOOL = ctypes.c_int
_WIN_STRING = ctypes.c_wchar_p

_SECURITY_CAPABILITIES = 0x00020009
_CHILD_PROCESS_POLICY = 0x0002000E
_ALL_APPLICATION_PACKAGES_POLICY = 0x0002000F
_PROCESS_CREATION_CHILD_PROCESS_RESTRICTED = 1
_PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT = 1
_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_TOKEN_QUERY = 0x0008
_TOKEN_IS_APP_CONTAINER = 29
_ERROR_INSUFFICIENT_BUFFER = 122
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102


class Unavailable(OSError):
    """The restricted launch could not be established."""


class _SecurityCapabilities(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", _POINTER),
        ("Capabilities", _POINTER),
        ("CapabilityCount", _DWORD),
        ("Reserved", _DWORD),
    ]


def _win_checked(ok) -> None:
    if not ok:
        raise Unavailable(str(ctypes.WinError(ctypes.get_last_error())))


@lru_cache(maxsize=1)
def _apis():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv.CreateAppContainerProfile.argtypes = [
        _WIN_STRING,
        _WIN_STRING,
        _WIN_STRING,
        _POINTER,
        _DWORD,
        ctypes.POINTER(_POINTER),
    ]
    userenv.DeleteAppContainerProfile.argtypes = [_WIN_STRING]
    security.ConvertSidToStringSidW.argtypes = [_POINTER, ctypes.POINTER(_WIN_STRING)]
    security.FreeSid.argtypes = [_POINTER]
    security.FreeSid.restype = _POINTER
    security.OpenProcessToken.argtypes = [_POINTER, _DWORD, ctypes.POINTER(_POINTER)]
    security.GetTokenInformation.argtypes = [
        _POINTER,
        _DWORD,
        _POINTER,
        _DWORD,
        ctypes.POINTER(_DWORD),
    ]
    kernel.LocalFree.argtypes = [_POINTER]
    kernel.LocalFree.restype = _POINTER
    kernel.CloseHandle.argtypes = [_POINTER]
    kernel.InitializeProcThreadAttributeList.argtypes = [
        _POINTER,
        _DWORD,
        _DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel.UpdateProcThreadAttribute.argtypes = [
        _POINTER,
        _DWORD,
        ctypes.c_size_t,
        _POINTER,
        ctypes.c_size_t,
        _POINTER,
        _POINTER,
    ]
    kernel.DeleteProcThreadAttributeList.argtypes = [_POINTER]
    kernel.DeleteProcThreadAttributeList.restype = None
    kernel.CreateProcessW.argtypes = [
        _WIN_STRING,
        _WIN_STRING,
        _POINTER,
        _POINTER,
        _BOOL,
        _DWORD,
        _POINTER,
        _WIN_STRING,
        _POINTER,
        _POINTER,
    ]
    kernel.ResumeThread.argtypes = [_POINTER]
    kernel.ResumeThread.restype = _DWORD
    kernel.WaitForSingleObject.argtypes = [_POINTER, _DWORD]
    kernel.WaitForSingleObject.restype = _DWORD
    kernel.GetExitCodeProcess.argtypes = [_POINTER, ctypes.POINTER(_DWORD)]
    kernel.TerminateProcess.argtypes = [_POINTER, _DWORD]
    return kernel, userenv, security


def available() -> bool:
    if os.name != "nt":
        return False
    try:
        _apis()
    except (AttributeError, OSError):
        return False
    return True


@contextmanager
def _profile(kernel, userenv, security):
    name = f"Tavotto.OfflineReplay.{uuid.uuid4().hex}"
    sid, label = _POINTER(), _WIN_STRING()
    status = userenv.CreateAppContainerProfile(
        name, name, "Tavotto offline replay", None, 0, ctypes.byref(sid)
    )
    if status:
        raise Unavailable(f"Creating an AppContainer returned 0x{status & 0xFFFFFFFF:08x}.")
    try:
        _win_checked(security.ConvertSidToStringSidW(sid, ctypes.byref(label)))
        yield sid, label.value
    finally:
        if label:
            kernel.LocalFree(ctypes.cast(label, _POINTER))
        security.FreeSid(sid)
        status = userenv.DeleteAppContainerProfile(name)
        if status:
            raise Unavailable(f"Deleting the AppContainer returned 0x{status & 0xFFFFFFFF:08x}.")


@contextmanager
def _start_attributes(kernel, sid):
    attributes = {
        _SECURITY_CAPABILITIES: _SecurityCapabilities(sid, None, 0, 0),
        _CHILD_PROCESS_POLICY: _DWORD(_PROCESS_CREATION_CHILD_PROCESS_RESTRICTED),
        _ALL_APPLICATION_PACKAGES_POLICY: _DWORD(
            _PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT
        ),
    }
    size = ctypes.c_size_t()
    measured = kernel.InitializeProcThreadAttributeList(
        None, len(attributes), 0, ctypes.byref(size)
    )
    if measured or not size.value or ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
        raise Unavailable("The extended startup attribute size could not be verified.")
    storage = ctypes.create_string_buffer(size.value)
    _win_checked(
        kernel.InitializeProcThreadAttributeList(storage, len(attributes), 0, ctypes.byref(size))
    )
    try:
        for kind, value in attributes.items():
            _win_checked(
                kernel.UpdateProcThreadAttribute(
                    storage, 0, kind, ctypes.byref(value), ctypes.sizeof(value), None, None
                )
            )
        yield ctypes.cast(storage, _POINTER)
    finally:
        kernel.DeleteProcThreadAttributeList(storage)


def _verify_appcontainer(kernel, security, process) -> None:
    token = _POINTER()
    _win_checked(security.OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token)))
    try:
        value, size = _DWORD(), _DWORD()
        _win_checked(
            security.GetTokenInformation(
                token,
                _TOKEN_IS_APP_CONTAINER,
                ctypes.byref(value),
                ctypes.sizeof(value),
                ctypes.byref(size),
            )
        )
        if size.value != ctypes.sizeof(value) or value.value != 1:
            raise Unavailable("The new process does not have an AppContainer token.")
    finally:
        kernel.CloseHandle(token)


def _run(
    kernel, security, sid, argv: list[str], cwd: Path, env: dict[str, str], timeout: float
) -> int:
    structs = ProcessStructures.get()
    info = structs.STARTUPINFOEXW()
    info.StartupInfo.cb = ctypes.sizeof(info)
    process = structs.PROCESS_INFORMATION()
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
    items = sorted(env.items(), key=lambda item: item[0].upper())
    environment = ctypes.create_unicode_buffer(
        "\0".join(f"{key}={value}" for key, value in items) + "\0"
    )
    flags = (
        CREATE_NO_WINDOW
        | _CREATE_SUSPENDED
        | _CREATE_UNICODE_ENVIRONMENT
        | EXTENDED_STARTUPINFO_PRESENT
    )
    exited = False
    try:
        with _start_attributes(kernel, sid) as attributes:
            info.lpAttributeList = attributes
            _win_checked(
                kernel.CreateProcessW(
                    argv[0],
                    command,
                    None,
                    None,
                    False,
                    flags,
                    environment,
                    str(cwd),
                    ctypes.byref(info),
                    ctypes.byref(process),
                )
            )
        _verify_appcontainer(kernel, security, process.hProcess)
        if kernel.ResumeThread(process.hThread) != 1:
            raise Unavailable("The isolated process was not resumed from its initial suspension.")
        waited = kernel.WaitForSingleObject(process.hProcess, max(0, int(timeout * 1000)))
        if waited == _WAIT_TIMEOUT:
            raise TimeoutError("The isolated process timed out.")
        if waited != _WAIT_OBJECT_0:
            raise OSError("Could not confirm the isolated process exited.")
        exited = True
        status = _DWORD()
        if not kernel.GetExitCodeProcess(process.hProcess, ctypes.byref(status)):
            raise OSError("Could not read the isolated process exit status.")
        return status.value
    finally:
        if process.hProcess and not exited:
            kernel.TerminateProcess(process.hProcess, 1)
            kernel.WaitForSingleObject(process.hProcess, 5000)
        for handle in (process.hThread, process.hProcess):
            if handle:
                kernel.CloseHandle(handle)


@contextmanager
def offline(workspace: Path, environment: dict[str, str]):
    """Give the container access only to the caller's disposable workspace."""
    kernel, userenv, security = _apis()
    root = workspace.resolve(strict=True)
    system = Path(os.environ["SystemRoot"]).resolve(strict=True)
    acl = system / "System32" / "icacls.exe"
    with _profile(kernel, userenv, security) as (sid, label):
        for arguments in (("/grant", f"*{label}:(OI)(CI)M"), ("/setintegritylevel", "(OI)(CI)L")):
            subprocess.run(  # noqa: S603 - a Windows OS binary with fixed options and a private workspace
                [str(acl), str(root), *arguments, "/T", "/Q"],
                creationflags=CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=60,
            )

        def execute(argv: list[str], cwd: Path, *, timeout: float) -> int:
            cwd = cwd.resolve(strict=True)
            program = Path(argv[0]).resolve(strict=True)
            if not (cwd.is_relative_to(root) and program.is_relative_to(root)):
                raise Unavailable("The isolated command must run inside its private workspace.")
            return _run(kernel, security, sid, [str(program), *argv[1:]], cwd, environment, timeout)

        yield execute
