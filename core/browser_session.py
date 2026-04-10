import time
import uuid
from datetime import UTC, datetime
from threading import Condition

from core.browser_runtime import get_novnc_url, get_profile_dir


SESSION_CONDITION = Condition()
SESSION_STATE = {
    "session_id": None,
    "source": None,
    "mode": None,
    "status": "idle",
    "message": "Chua co browser session nao dang chay.",
    "profile_path": None,
    "vnc_url": None,
    "updated_at": datetime.now(UTC).isoformat(),
}
ACTIVE_STATUSES = {"needs_user_action", "waiting_for_verification"}


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _snapshot() -> dict:
    return dict(SESSION_STATE)


def get_session_state(host: str = "localhost", scheme: str = "http") -> dict:
    with SESSION_CONDITION:
        state = _snapshot()

    state["vnc_url"] = state["vnc_url"] or get_novnc_url(host=host, scheme=scheme)
    return state


def set_session_state(
    *,
    source: str,
    mode: str,
    status: str,
    message: str,
    host: str = "localhost",
    scheme: str = "http",
) -> dict:
    with SESSION_CONDITION:
        if SESSION_STATE["status"] in ACTIVE_STATUSES:
            raise RuntimeError("Dang co browser session khac can nguoi dung thao tac.")

        SESSION_STATE.update(
            {
                "session_id": uuid.uuid4().hex,
                "source": source,
                "mode": mode,
                "status": status,
                "message": message,
                "profile_path": str(get_profile_dir(source)),
                "vnc_url": get_novnc_url(host=host, scheme=scheme),
                "updated_at": _timestamp(),
            }
        )
        SESSION_CONDITION.notify_all()
        return _snapshot()


def mark_session_ready(message: str) -> dict:
    with SESSION_CONDITION:
        if SESSION_STATE["status"] == "idle":
            raise RuntimeError("Khong co browser session nao de tiep tuc.")

        SESSION_STATE["status"] = "ready"
        SESSION_STATE["message"] = message
        SESSION_STATE["updated_at"] = _timestamp()
        SESSION_CONDITION.notify_all()
        return _snapshot()


def fail_session(message: str) -> dict:
    with SESSION_CONDITION:
        SESSION_STATE["status"] = "failed"
        SESSION_STATE["message"] = message
        SESSION_STATE["updated_at"] = _timestamp()
        SESSION_CONDITION.notify_all()
        return _snapshot()


def reset_session(message: str = "Browser session da dung.") -> dict:
    with SESSION_CONDITION:
        SESSION_STATE.update(
            {
                "session_id": None,
                "source": None,
                "mode": None,
                "status": "idle",
                "message": message,
                "profile_path": None,
                "vnc_url": None,
                "updated_at": _timestamp(),
            }
        )
        SESSION_CONDITION.notify_all()
        return _snapshot()


def wait_for_user_action(
    *,
    source: str,
    mode: str,
    message: str,
    timeout_seconds: float,
    host: str = "localhost",
    scheme: str = "http",
) -> None:
    with SESSION_CONDITION:
        SESSION_STATE.update(
            {
                "session_id": uuid.uuid4().hex,
                "source": source,
                "mode": mode,
                "status": "waiting_for_verification",
                "message": message,
                "profile_path": str(get_profile_dir(source)),
                "vnc_url": get_novnc_url(host=host, scheme=scheme),
                "updated_at": _timestamp(),
            }
        )
        session_id = SESSION_STATE["session_id"]
        deadline = time.monotonic() + timeout_seconds
        SESSION_CONDITION.notify_all()

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                SESSION_STATE["status"] = "failed"
                SESSION_STATE["message"] = "Het thoi gian cho nguoi dung hoan tat xac minh trong browser."
                SESSION_STATE["updated_at"] = _timestamp()
                SESSION_CONDITION.notify_all()
                raise RuntimeError(SESSION_STATE["message"])

            SESSION_CONDITION.wait(timeout=min(0.5, remaining))

            if SESSION_STATE["session_id"] != session_id:
                raise RuntimeError("Browser session da bi thay doi trong luc crawler dang cho xac minh.")

            if SESSION_STATE["status"] == "ready":
                return

            if SESSION_STATE["status"] == "failed":
                raise RuntimeError(SESSION_STATE["message"])
