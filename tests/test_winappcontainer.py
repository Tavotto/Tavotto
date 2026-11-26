"""The staged command stays suspended until the new process token is verified."""

from contextlib import contextmanager

import pytest

from tavotto.engine.fixtools import winappcontainer


def test_configures_no_capabilities_and_restricts_child_processes(monkeypatch):
    observed = {}

    class Kernel:
        def InitializeProcThreadAttributeList(self, storage, count, _flags, size):
            assert count == 3
            size._obj.value = 512
            return storage is not None

        def UpdateProcThreadAttribute(self, _storage, _flags, kind, value, *_args):
            observed[kind] = value._obj
            return True

        def DeleteProcThreadAttributeList(self, _storage):
            pass

    monkeypatch.setattr(winappcontainer.ctypes, "get_last_error", lambda: 122, raising=False)
    with winappcontainer._start_attributes(Kernel(), 10) as attributes:
        assert attributes.value
        security = observed[winappcontainer._SECURITY_CAPABILITIES]
        assert security.AppContainerSid == 10
        assert security.CapabilityCount == 0
        assert security.Capabilities is None
        assert observed[winappcontainer._CHILD_PROCESS_POLICY].value == 1
        assert observed[winappcontainer._ALL_APPLICATION_PACKAGES_POLICY].value == 1


@pytest.mark.parametrize("isolated", [True, False])
def test_create_and_suspend_before_verifying_the_appcontainer(tmp_path, monkeypatch, isolated):
    events = []

    @contextmanager
    def attributes(_kernel, _sid):
        events.append("attributes")
        yield None

    class Kernel:
        def CreateProcessW(self, _program, _line, _access, _thread, inherits, flags, *_args):
            assert not inherits
            assert flags & winappcontainer._CREATE_SUSPENDED
            assert flags & winappcontainer.EXTENDED_STARTUPINFO_PRESENT
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
    arguments = (Kernel(), object(), None, ["Rterm.exe"], tmp_path, {}, 1.0)
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
