"""The staged command stays suspended until the new process token is verified."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tavotto.engine.fixtools import winappcontainer


def test_configures_no_capabilities_and_restricts_child_processes(monkeypatch):
    observed = {}

    class Kernel:
        def InitializeProcThreadAttributeList(self, storage, count, _flags, size):
            assert count == 4
            size._obj.value = 512
            return storage is not None

        def UpdateProcThreadAttribute(self, _storage, _flags, kind, value, *_args):
            observed[kind] = value._obj
            return True

        def DeleteProcThreadAttributeList(self, _storage):
            pass

    monkeypatch.setattr(winappcontainer.ctypes, "get_last_error", lambda: 122, raising=False)
    with winappcontainer._start_attributes(Kernel(), 10, (20, 21)) as attributes:
        assert attributes.value
        assert set(observed[winappcontainer._HANDLE_LIST]) == {20, 21}
        security = observed[winappcontainer._SECURITY_CAPABILITIES]
        assert security.AppContainerSid == 10
        assert security.CapabilityCount == 0
        assert security.Capabilities is None
        assert observed[winappcontainer._CHILD_PROCESS_POLICY].value == 1
        assert observed[winappcontainer._ALL_APPLICATION_PACKAGES_POLICY].value == 1


@pytest.mark.parametrize("can_name_station", [True, False])
def test_replay_desktop_has_its_own_access_and_lifetime(monkeypatch, can_name_station):
    events = []
    user = "S-1-5-21-100"
    package = "S-1-15-2-200"

    def descriptor(sddl, _version, out, _length):
        assert sddl == f"D:P(A;;GA;;;SY)(A;;GA;;;{user})(A;;GA;;;{package})S:(ML;;NW;;;LW)"
        out._obj.value = 5
        return 1

    def create_station(name, flags, _access, attributes):
        events.append(("create", bool(name)))
        assert flags == winappcontainer._CREATE_STATION_ONLY
        assert not attributes._obj.bInheritHandle
        assert attributes._obj.lpSecurityDescriptor == 5
        return 20 if can_name_station or name is None else None

    def create_desktop(name, _device, _mode, _flags, access, attributes):
        assert name == "Default"
        assert access & winappcontainer._DESKTOP_CREATE_WINDOW
        assert not attributes._obj.bInheritHandle
        assert attributes._obj.lpSecurityDescriptor == 5
        events.append("create desktop")
        return 30

    desktop_api = SimpleNamespace(
        GetProcessWindowStation=lambda: 10,
        GetUserObjectInformationW=lambda *_: 0,
        CreateWindowStationW=create_station,
        SetProcessWindowStation=lambda handle: events.append(("switch", handle)) or 1,
        CloseWindowStation=lambda handle: events.append(("close station", handle)),
        CreateDesktopW=create_desktop,
        CloseDesktop=lambda handle: events.append(("close desktop", handle)),
    )
    kernel = SimpleNamespace(LocalFree=lambda pointer: events.append(("free", pointer.value)))
    security = SimpleNamespace(ConvertStringSecurityDescriptorToSecurityDescriptorW=descriptor)
    monkeypatch.setattr(
        winappcontainer.ctypes, "WinDLL", lambda *a, **k: desktop_api, raising=False
    )
    monkeypatch.setattr(winappcontainer.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(winappcontainer, "_current_user_sid", lambda *_: user)
    monkeypatch.setattr(winappcontainer, "_station_name", lambda *_: "Service-station")

    with winappcontainer._private_desktop(kernel, security, package) as desktop:
        assert desktop.endswith(r"\Default")
        if not can_name_station:
            assert desktop == r"Service-station\Default"
        assert events[-1] == ("switch", 10)
        events.append("isolated command")

    attempts = [("create", True)] if can_name_station else [("create", True), ("create", False)]
    assert events == [
        *attempts,
        ("switch", 20),
        "create desktop",
        ("switch", 10),
        "isolated command",
        ("close desktop", 30),
        ("close station", 20),
        ("free", 5),
    ]


@pytest.mark.parametrize("isolated", [True, False])
def test_create_and_suspend_before_verifying_the_appcontainer(tmp_path, monkeypatch, isolated):
    events = []

    @contextmanager
    def attributes(_kernel, _sid, handles):
        assert handles == (20, 21)
        events.append("attributes")
        yield None

    class Kernel:
        def CreateProcessW(self, _program, _line, _access, _thread, inherits, flags, *_args):
            assert inherits
            assert flags & winappcontainer._CREATE_SUSPENDED
            assert flags & winappcontainer.EXTENDED_STARTUPINFO_PRESENT
            assert flags & winappcontainer._DETACHED_PROCESS
            assert not flags & 0x08000000  # CREATE_NO_WINDOW
            startup = _args[-2]._obj.StartupInfo
            assert startup.lpDesktop == "TavottoReplay-test\\Default"
            assert startup.dwFlags & winappcontainer._USE_STD_HANDLES
            assert (startup.hStdInput, startup.hStdOutput, startup.hStdError) == (20, 21, 21)
            output = _args[-1]._obj
            output.hProcess, output.hThread = 10, 11
            events.append("created")
            return 1

        def ResumeThread(self, thread):
            assert thread == 11
            events.append("resumed")
            return 1

        def WaitForSingleObject(self, handle, _timeout):
            assert handle == 10
            events.append("waited")
            return 0

        def GetExitCodeProcess(self, handle, result):
            assert handle == 10
            result._obj.value = 0
            return 1

        def TerminateProcess(self, handle, _exit_code):
            assert handle == 10
            events.append("terminated")

        def CloseHandle(self, handle):
            events.append(("closed", handle))

    def verify(_kernel, _security, process):
        assert process == 10
        events.append("verified")
        if not isolated:
            raise winappcontainer.Unavailable("Unverified AppContainer")

    monkeypatch.setattr(winappcontainer, "_start_attributes", attributes)
    monkeypatch.setattr(winappcontainer, "_verify_appcontainer", verify)
    arguments = (
        Kernel(),
        object(),
        None,
        ["Rterm.exe"],
        tmp_path,
        {},
        1.0,
        "TavottoReplay-test\\Default",
        (20, 21),
    )
    if isolated:
        assert winappcontainer._run(*arguments) == 0
    else:
        with pytest.raises(winappcontainer.Unavailable, match="Unverified AppContainer"):
            winappcontainer._run(*arguments)
    assert events == [
        "attributes",
        "created",
        "verified",
        "resumed" if isolated else "terminated",
        "waited",
        ("closed", 11),
        ("closed", 10),
    ]
