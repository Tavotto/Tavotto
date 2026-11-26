"""One-time invitations for an explicitly selected plot in an existing R console."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

from . import config, envlease, execspec, nativeggplot, nativerelay, nativesession, runcodes

_INVITATION_SECONDS = 180
_LOG = logging.getLogger(__name__)


def _command(adapter: Path, offer: Path, object_name: str) -> str:
    # Use only ASCII escapes so the R command can also be pasted into a
    # terminal whose encoding differs from the project's path.
    def quote(value: str) -> str:
        return "".join(
            char if char.isascii() else f"\\U{ord(char):08x}"
            for char in json.dumps(value, ensure_ascii=False)
        )

    return (
        "local({bridge <- new.env(parent = baseenv()); "
        f"sys.source({quote(adapter.as_posix())}, envir = bridge); "
        f"bridge$tavotto_attach(get({quote(object_name)}, envir = .GlobalEnv, inherits = FALSE), "
        f"{quote(offer.as_posix())})}})"
    )


def invite(
    project_root: str,
    script: str,
    target: nativeggplot.GgplotTarget,
    *,
    registry: nativesession.NativeSessionRegistry,
    project_epoch: int,
) -> dict:
    session_id = f"native-r-console-{uuid.uuid4().hex}"
    ticket = registry.begin_start(session_id, project_root, script, project_epoch)
    relay = None
    offer = None
    try:
        out_dir = config.data_path("cache", "native", session_id).resolve()
        out_dir.mkdir(parents=True, mode=0o700)
        offer = out_dir / "invitation.json"
        relay = nativerelay.NativeRelay()
        bridge = nativeggplot.GGPLOT_BRIDGE
        registry.attach_owner(ticket, SimpleNamespace(terminate_sync=relay.cancel))
        payload = {
            "token": relay.child_token,
            "spec": {
                "console": True,
                "host": relay.host,
                "port": relay.child_port,
                "adapter": (bridge.parent / "ggplot2.R").as_posix(),
                "stem": target.stem(script),
                "frame_pt": target.frame_pt,
                "out_dir": out_dir.as_posix(),
            },
        }
        fd = os.open(offer, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as invitation:
            json.dump(payload, invitation)
        thread = threading.Thread(
            target=_accept,
            args=(registry, ticket, relay, offer, project_root, script, target),
            name="tavotto-r-console",
            daemon=True,
        )
        thread.start()
        return {
            "command": _command(bridge, offer, target.object_name),
            "expires_in": _INVITATION_SECONDS,
        }
    except BaseException:
        if relay is not None:
            relay.close()
        if offer is not None:
            offer.unlink(missing_ok=True)
        registry.end_start(ticket)
        raise


def _accept(registry, ticket, relay, offer, project_root, script, target) -> None:
    transport = None
    interpreter = None
    sock = None
    adopted = False
    try:
        sock, hello = relay.adopt_child(
            _INVITATION_SECONDS, watch=lambda: registry.check_start(ticket)
        )
        registry.check_start(ticket)
        value = hello.get("interpreter")
        if not isinstance(value, str):
            return
        interpreter_path = Path(value)
        if (
            not interpreter_path.is_absolute()
            or interpreter_path.name.lower() not in {"rscript", "rscript.exe"}
            or not interpreter_path.is_file()
        ):
            return
        interpreter = str(interpreter_path)
        session_id = ticket.launch_id
        envlease.acquire_native(interpreter, session_id)
        session = nativesession.NativeSession(
            session_id=session_id,
            descriptor={
                "native_id": session_id,
                "out_dir": str(offer.parent),
                "metadata": {
                    "project_root": project_root,
                    "target_kind": execspec.TARGET_SCRIPT,
                    "target_display": script,
                    "interpreter": interpreter,
                },
            },
            ggplot_target=target,
        )
        transport = nativesession.NativeTransport(
            sock,
            on_event=session._on_frame,  # noqa: SLF001
            on_close=session._on_transport_closed,  # noqa: SLF001
        )
        session.transport = transport
        session.note_hello(hello)
        transport.start()
        registry.adopt(session, ticket=ticket)
        adopted = True
    except (runcodes.RunError, nativerelay.RelayClosed, envlease.EnvironmentBusy):
        # An expired or cancelled invitation only closes this connection.
        pass
    except Exception:
        _LOG.warning("Could not accept a local R console invitation", exc_info=True)
    finally:
        relay.close()
        if not adopted:
            if transport is not None:
                transport.close()
            elif sock is not None:
                sock.close()
            if interpreter is not None:
                envlease.release_native(interpreter, ticket.launch_id)
        try:
            offer.unlink(missing_ok=True)
        finally:
            registry.end_start(ticket)
