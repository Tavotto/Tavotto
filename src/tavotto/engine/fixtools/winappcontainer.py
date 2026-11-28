"""Run a staged command inside a disposable, offline Windows AppContainer."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from ..runtime import CREATE_NO_WINDOW, is_frozen
from ..winprocess import EXTENDED_STARTUPINFO_PRESENT, ProcessStructures

_POINTER = ctypes.c_void_p
_DWORD = ctypes.c_uint32
_BOOL = ctypes.c_int
_WIN_STRING = ctypes.c_wchar_p

_HANDLE_LIST = 0x00020002
_SECURITY_CAPABILITIES = 0x00020009
_CHILD_PROCESS_POLICY = 0x0002000E
_ALL_APPLICATION_PACKAGES_POLICY = 0x0002000F
_PROCESS_CREATION_CHILD_PROCESS_RESTRICTED = 1
_PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT = 1
_CREATE_SUSPENDED = 0x00000004
_DETACHED_PROCESS = 0x00000008
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_TOKEN_IS_APP_CONTAINER = 29
_ERROR_ACCESS_DENIED = 5
_ERROR_INSUFFICIENT_BUFFER = 122
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102
_GENERIC_ALL = 0x10000000
_CREATE_STATION_ONLY = 1
_DESKTOP_CREATE_WINDOW = 0x0002
_USER_OBJECT_NAME = 2
_USE_STD_HANDLES = 0x00000100
_OUTPUT_TAIL_BYTES = 4096

CHILD_FLAG = "--win-offline-r-child"


class Unavailable(OSError):
    """The restricted launch could not be established."""


class ReplayFailed(OSError):
    def __init__(self, status: int, diagnostic: str):
        super().__init__(f"The isolated R process exited with status {status}.")
        self.diagnostic = diagnostic


class _SecurityCapabilities(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", _POINTER),
        ("Capabilities", _POINTER),
        ("CapabilityCount", _DWORD),
        ("Reserved", _DWORD),
    ]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", _DWORD),
        ("lpSecurityDescriptor", _POINTER),
        ("bInheritHandle", _BOOL),
    ]


def _win_checked(ok, action: str | None = None) -> None:
    if not ok:
        error = str(ctypes.WinError(ctypes.get_last_error()))
        raise Unavailable(f"{action}: {error}" if action else error)


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
    security.ConvertStringSidToSidW.argtypes = [_WIN_STRING, ctypes.POINTER(_POINTER)]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        _WIN_STRING,
        _DWORD,
        ctypes.POINTER(_POINTER),
        ctypes.POINTER(_DWORD),
    ]
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
    kernel.GetCurrentProcess.restype = _POINTER
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


def _current_user_sid(kernel, security) -> str:
    token = _POINTER()
    _win_checked(
        security.OpenProcessToken(kernel.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token))
    )
    label = _WIN_STRING()
    try:
        size = _DWORD()
        measured = security.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        if measured or not size.value or ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
            raise Unavailable("Could not determine the current Windows user SID size.")
        data = ctypes.create_string_buffer(size.value)
        _win_checked(
            security.GetTokenInformation(token, _TOKEN_USER, data, size.value, ctypes.byref(size))
        )
        # TOKEN_USER starts with SID_AND_ATTRIBUTES whose first field is the SID pointer.
        user = ctypes.cast(data, ctypes.POINTER(_POINTER)).contents
        _win_checked(security.ConvertSidToStringSidW(user, ctypes.byref(label)))
        return label.value
    finally:
        if label:
            kernel.LocalFree(ctypes.cast(label, _POINTER))
        kernel.CloseHandle(token)


def _station_name(user32, station) -> str:
    size = _DWORD()
    measured = user32.GetUserObjectInformationW(
        station, _USER_OBJECT_NAME, None, 0, ctypes.byref(size)
    )
    if measured or not size.value or ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
        raise Unavailable("Could not determine the private Windows station name size.")
    name = ctypes.create_unicode_buffer(size.value // ctypes.sizeof(ctypes.c_wchar))
    _win_checked(
        user32.GetUserObjectInformationW(
            station, _USER_OBJECT_NAME, name, ctypes.sizeof(name), ctypes.byref(size)
        )
    )
    return name.value


@contextmanager
def _private_desktop(kernel, security, package_sid):
    """Give USER32 a Low-integrity desktop without exposing the caller's desktop."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetProcessWindowStation.restype = _POINTER
    user32.GetUserObjectInformationW.argtypes = [
        _POINTER,
        ctypes.c_int,
        _POINTER,
        _DWORD,
        ctypes.POINTER(_DWORD),
    ]
    user32.CreateWindowStationW.argtypes = [
        _WIN_STRING,
        _DWORD,
        _DWORD,
        ctypes.POINTER(_SecurityAttributes),
    ]
    user32.CreateWindowStationW.restype = _POINTER
    user32.SetProcessWindowStation.argtypes = [_POINTER]
    user32.CloseWindowStation.argtypes = [_POINTER]
    user32.CreateDesktopW.argtypes = [
        _WIN_STRING,
        _WIN_STRING,
        _POINTER,
        _DWORD,
        _DWORD,
        ctypes.POINTER(_SecurityAttributes),
    ]
    user32.CreateDesktopW.restype = _POINTER
    user32.CloseDesktop.argtypes = [_POINTER]
    caller = user32.GetProcessWindowStation()
    _win_checked(caller)
    user = _current_user_sid(kernel, security)
    sddl = f"D:P(A;;GA;;;SY)(A;;GA;;;{user})(A;;GA;;;{package_sid})S:(ML;;NW;;;LW)"
    descriptor = _POINTER()
    _win_checked(
        security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None
        )
    )
    access = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
    name = "TavottoReplay-" + uuid.uuid4().hex
    station = desktop = None
    try:
        station = user32.CreateWindowStationW(
            name, _CREATE_STATION_ONLY, _GENERIC_ALL, ctypes.byref(access)
        )
        if not station and ctypes.get_last_error() == _ERROR_ACCESS_DENIED:
            # Standard users can create the station named by Windows for
            # their logon. CREATE_ONLY protects any already existing station.
            station = user32.CreateWindowStationW(
                None, _CREATE_STATION_ONLY, _GENERIC_ALL, ctypes.byref(access)
            )
            _win_checked(station, "Create the private logon window station")
            name = _station_name(user32, station)
        _win_checked(station, "Create the private window station")
        # Desktop creation uses the process's current station. Only the
        # dedicated helper uses this function, so other application threads
        # cannot switch desktops or launch children during this interval.
        _win_checked(user32.SetProcessWindowStation(station), "Enter the private window station")
        try:
            desktop = user32.CreateDesktopW(
                "Default",
                None,
                None,
                0,
                _GENERIC_ALL | _DESKTOP_CREATE_WINDOW,
                ctypes.byref(access),
            )
            _win_checked(desktop, "Create the private replay desktop")
        finally:
            _win_checked(
                user32.SetProcessWindowStation(caller), "Restore the original window station"
            )
        yield f"{name}\\Default"
    finally:
        if desktop:
            user32.CloseDesktop(desktop)
        if station:
            user32.CloseWindowStation(station)
        kernel.LocalFree(descriptor)


@contextmanager
def _start_attributes(kernel, sid, handles: tuple[int, int]):
    attributes = {
        _HANDLE_LIST: (_POINTER * len(handles))(*handles),
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
                ),
                "Set an AppContainer process attribute",
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
    kernel,
    security,
    sid,
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    desktop: str,
    handles: tuple[int, int],
) -> int:
    structs = ProcessStructures.get()
    info = structs.STARTUPINFOEXW()
    info.StartupInfo.cb = ctypes.sizeof(info)
    info.StartupInfo.lpDesktop = desktop
    info.StartupInfo.dwFlags = _USE_STD_HANDLES
    info.StartupInfo.hStdInput = handles[0]
    info.StartupInfo.hStdOutput = info.StartupInfo.hStdError = handles[1]
    process = structs.PROCESS_INFORMATION()
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
    items = sorted(env.items(), key=lambda item: item[0].upper())
    environment = ctypes.create_unicode_buffer(
        "\0".join(f"{key}={value}" for key, value in items) + "\0"
    )
    # Attaching a hidden or new console fails during DLL initialization in
    # the LPAC. Rterm reads the staged driver directly and needs no console.
    flags = (
        _DETACHED_PROCESS
        | _CREATE_SUSPENDED
        | _CREATE_UNICODE_ENVIRONMENT
        | EXTENDED_STARTUPINFO_PRESENT
    )
    exited = False
    try:
        with _start_attributes(kernel, sid, handles) as attributes:
            info.lpAttributeList = attributes
            _win_checked(
                kernel.CreateProcessW(
                    argv[0],
                    command,
                    None,
                    None,
                    True,
                    flags,
                    environment,
                    str(cwd),
                    ctypes.byref(info),
                    ctypes.byref(process),
                ),
                "Create the suspended AppContainer process",
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


def _workspace_command(root: Path, argv: list[str], cwd: Path) -> tuple[list[str], Path]:
    cwd = cwd.resolve(strict=True)
    program = Path(argv[0]).resolve(strict=True)
    if not (cwd.is_relative_to(root) and program.is_relative_to(root)):
        raise Unavailable("The isolated command must run inside its private workspace.")
    return [str(program), *argv[1:]], cwd


def _run_with_workspace_streams(kernel, security, sid, package_sid, root, argv, cwd, request):
    import msvcrt

    # The only inherited handles point to two disposable files. The empty
    # input and private output also give Rterm valid non-console stdio.
    with tempfile.TemporaryFile(dir=root) as empty, tempfile.TemporaryFile(dir=root) as log:
        handles = msvcrt.get_osfhandle(empty.fileno()), msvcrt.get_osfhandle(log.fileno())
        for handle in handles:
            os.set_handle_inheritable(handle, True)
        with _private_desktop(kernel, security, package_sid) as desktop:
            status = _run(
                kernel,
                security,
                sid,
                argv,
                cwd,
                request["env"],
                request["timeout"],
                desktop,
                handles,
            )
        result = {"status": status}
        if status:
            length = log.seek(0, os.SEEK_END)
            log.seek(max(0, length - _OUTPUT_TAIL_BYTES))
            result["diagnostic"] = log.read(_OUTPUT_TAIL_BYTES).decode("utf-8", "replace")
        return result


def child_main() -> int:
    """Use a private desktop in a short-lived broker process."""
    if not available():
        return 2
    try:
        request = json.load(sys.stdin)
        root = Path(request["workspace"]).resolve(strict=True)
        argv, cwd = _workspace_command(root, request["argv"], Path(request["cwd"]))
        package_sid = request["sid"]
        if not package_sid.startswith("S-1-15-2-") or not all(
            part.isascii() and part.isdigit() for part in package_sid.split("-")[1:]
        ):
            raise Unavailable("Invalid AppContainer SID.")
        kernel, _, security = _apis()
        sid = _POINTER()
        _win_checked(security.ConvertStringSidToSidW(package_sid, ctypes.byref(sid)))
        try:
            result = _run_with_workspace_streams(
                kernel, security, sid, package_sid, root, argv, cwd, request
            )
        finally:
            kernel.LocalFree(sid)
    except (OSError, ValueError, IndexError, TypeError, AttributeError, KeyError) as exc:
        result = {"error": str(exc)}
    print(json.dumps(result), flush=True)
    return 0


def _isolated_desktop_command(
    root: Path, argv: list[str], cwd: Path, environment: dict[str, str], timeout: float, sid: str
) -> None:
    if is_frozen():
        host = [sys.executable, CHILD_FLAG]
    else:
        host = [sys.executable, "-I", "-B", "-m", "tavotto.engine.fixtools.winappcontainer"]
    try:
        completed = subprocess.run(  # noqa: S603 - the dedicated broker is part of the current engine
            host,
            input=json.dumps(
                {
                    "workspace": str(root),
                    "argv": argv,
                    "cwd": str(cwd),
                    "env": environment,
                    "timeout": timeout,
                    "sid": sid,
                }
            ),
            encoding="utf-8",
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            check=True,
            timeout=timeout + 15,
        )
        result = json.loads(completed.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise Unavailable("The isolated Windows replay broker did not respond.") from exc
    if not isinstance(result, dict):
        raise Unavailable("The isolated Windows replay broker returned an invalid response.")
    if "error" in result:
        raise Unavailable(result["error"])
    status = result.get("status")
    if type(status) is not int or not 0 <= status <= 0xFFFFFFFF:
        raise Unavailable("The isolated Windows replay broker returned an invalid status.")
    if status:
        raise ReplayFailed(status, result.get("diagnostic", ""))


@contextmanager
def offline(workspace: Path, environment: dict[str, str]):
    """Give the container access only to the caller's disposable workspace."""
    kernel, userenv, security = _apis()
    root = workspace.resolve(strict=True)
    system = Path(os.environ["SystemRoot"]).resolve(strict=True)
    acl = system / "System32" / "icacls.exe"
    with _profile(kernel, userenv, security) as (_, label):
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

        def execute(argv: list[str], cwd: Path, *, timeout: float) -> None:
            argv, cwd = _workspace_command(root, argv, cwd)
            _isolated_desktop_command(root, argv, cwd, environment, timeout, label)

        yield execute


if __name__ == "__main__":
    raise SystemExit(child_main())
