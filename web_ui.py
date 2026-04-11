import csv
import json
import queue
import threading
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from threading import Event, Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from core.browser_session import (
    fail_session,
    get_session_state,
    mark_session_ready,
    reset_session,
    set_session_state,
)
from core.crawl_control import CrawlStoppedError, crawl_stop_context, raise_if_stop_requested
from core.crawl_batdongsan_list import (
    crawl_categories_for_today as crawl_batdongsan_categories_for_today,
    crawl_listing_page as crawl_batdongsan_listing_page,
)
from core.crawl_nhatot_list import crawl_listing_page as crawl_nhatot_listing_page
from core.login_batdongsan import HOME_URL, build_driver as build_batdongsan_login_driver
from main import normalize_rows, save_rows


app = FastAPI(title="Web Crawler UI")
WORKSPACE_ROOT = Path.cwd().resolve()
LOGIN_DRIVER = None
LOGIN_LOCK = Lock()
JOB_LOCK = Lock()
CRAWL_RUN_LOCK = Lock()
CRAWL_JOBS: dict[str, dict] = {}


class CrawlRequest(BaseModel):
    source: str
    output: str
    page_url: str | None = None
    page_number: int | None = None
    date: str | None = None


class CrawlStartResponse(BaseModel):
    job_id: str


class BrowserSessionStartRequest(BaseModel):
    source: str = "batdongsan.com"
    mode: str = "login"


class QueueTextWriter:
    def __init__(self, output_queue: queue.Queue[tuple[str, object]]):
        self.output_queue = output_queue
        self.buffer = ""

    def write(self, text: str) -> int:
        if not text:
            return 0

        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line:
                self.output_queue.put(("log", line))
        return len(text)

    def flush(self) -> None:
        if self.buffer:
            self.output_queue.put(("log", self.buffer))
            self.buffer = ""


def _sse_event(event: str, data: object) -> str:
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False)

    lines = data.splitlines() or [""]
    payload = [f"event: {event}"]
    payload.extend(f"data: {line}" for line in lines)
    payload.append("")
    return "\n".join(payload) + "\n"


def _store_job(job_id: str, job: dict) -> None:
    with JOB_LOCK:
        CRAWL_JOBS[job_id] = job


def _get_job(job_id: str) -> dict:
    with JOB_LOCK:
        job = CRAWL_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job crawl khong ton tai")
    return job


def _remove_job(job_id: str) -> None:
    with JOB_LOCK:
        CRAWL_JOBS.pop(job_id, None)


def _schedule_job_cleanup(job_id: str, delay_seconds: float = 600) -> None:
    timer = threading.Timer(delay_seconds, _remove_job, args=[job_id])
    timer.daemon = True
    timer.start()


def _request_origin(request: Request) -> tuple[str, str]:
    return request.url.hostname or "localhost", request.url.scheme or "http"


def _resolve_output_path(path_value: str) -> Path:
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = WORKSPACE_ROOT / candidate
    resolved = candidate.resolve()

    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Chi duoc tai file nam trong thu muc project.") from exc

    return resolved


def _count_csv_records(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        return sum(1 for row in reader if any(cell.strip() for cell in row))


def _build_partial_output(path_value: str | None) -> dict | None:
    if not path_value:
        return None

    try:
        resolved = _resolve_output_path(path_value)
    except HTTPException:
        return None

    if not resolved.is_file():
        return None

    try:
        count = _count_csv_records(resolved)
    except OSError:
        return None

    return {
        "output": path_value,
        "count": count,
    }


def _browser_session_response(request: Request) -> dict:
    host, scheme = _request_origin(request)
    state = get_session_state(host=host, scheme=scheme)
    with LOGIN_LOCK:
        login_driver_open = LOGIN_DRIVER is not None
    state["login_driver_open"] = login_driver_open
    return state


def _validate_browser_start_request(payload: BrowserSessionStartRequest) -> None:
    if payload.source != "batdongsan.com":
        raise HTTPException(status_code=400, detail="Hien tai chi ho tro mo browser login cho batdongsan.com")
    if payload.mode != "login":
        raise HTTPException(status_code=400, detail="mode khong hop le")


def _start_browser_session(payload: BrowserSessionStartRequest, request: Request) -> dict:
    global LOGIN_DRIVER

    _validate_browser_start_request(payload)
    host, scheme = _request_origin(request)

    try:
        set_session_state(
            source=payload.source,
            mode=payload.mode,
            status="needs_user_action",
            message="Browser da mo trong container. Hay dang nhap/xac minh trong noVNC, roi bam nut tiep tuc.",
            host=host,
            scheme=scheme,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    with LOGIN_LOCK:
        if LOGIN_DRIVER is not None:
            fail_session("Dang co browser login session khac dang mo.")
            raise HTTPException(status_code=409, detail="Dang co mot browser login session dang mo.")

        try:
            LOGIN_DRIVER = build_batdongsan_login_driver()
            LOGIN_DRIVER.get(HOME_URL)
        except Exception as exc:
            LOGIN_DRIVER = None
            fail_session(f"Khong mo duoc browser login: {exc}")
            raise HTTPException(status_code=500, detail=f"Khong mo duoc browser login: {exc}") from exc

    return _browser_session_response(request)


def _complete_browser_session(request: Request) -> dict:
    global LOGIN_DRIVER

    with LOGIN_LOCK:
        if LOGIN_DRIVER is not None:
            try:
                LOGIN_DRIVER.quit()
            except Exception as exc:
                LOGIN_DRIVER = None
                fail_session(f"Khong dong duoc browser login: {exc}")
                raise HTTPException(status_code=500, detail=f"Khong dong duoc browser login: {exc}") from exc
            LOGIN_DRIVER = None

    try:
        mark_session_ready("Nguoi dung da hoan tat login/xac minh. Crawler co the tiep tuc.")
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _browser_session_response(request)


def _stop_browser_session(request: Request) -> dict:
    global LOGIN_DRIVER

    stop_message = "Browser session da dung theo yeu cau nguoi dung."

    with LOGIN_LOCK:
        if LOGIN_DRIVER is not None:
            try:
                LOGIN_DRIVER.quit()
            except Exception as exc:
                LOGIN_DRIVER = None
                fail_session(f"Khong dong duoc browser login: {exc}")
                raise HTTPException(status_code=500, detail=f"Khong dong duoc browser login: {exc}") from exc
            LOGIN_DRIVER = None

    state = _browser_session_response(request)
    if state["status"] in {"needs_user_action", "waiting_for_verification"}:
        fail_session(stop_message)
        return _browser_session_response(request)

    reset_session(stop_message)
    return _browser_session_response(request)


def _validate_request(payload: CrawlRequest) -> None:
    valid_sources = {"batdongsan.com", "nhatot.com"}
    if payload.source not in valid_sources:
        raise HTTPException(status_code=400, detail="source khong hop le")

    if payload.source == "batdongsan.com":
        with LOGIN_LOCK:
            if LOGIN_DRIVER is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Dang co browser login Batdongsan dang mo. Hay bam nut tiep tuc hoac dung session truoc khi crawl.",
                )

    if payload.date == "today":
        if payload.source != "batdongsan.com":
            raise HTTPException(status_code=400, detail="date=today chi ho tro cho batdongsan.com")
        return

    if payload.date is not None:
        raise HTTPException(status_code=400, detail="date chi ho tro gia tri today")

    if not payload.page_url:
        raise HTTPException(status_code=400, detail="page_url la bat buoc neu khong dung today")

    if payload.page_number is None or payload.page_number < 1:
        raise HTTPException(status_code=400, detail="page_number phai >= 1")


def _execute_crawl(payload: CrawlRequest) -> dict:
    _validate_request(payload)

    output_path = Path(payload.output)
    page_url = payload.page_url
    page_number = payload.page_number

    if payload.date == "today":
        rows = crawl_batdongsan_categories_for_today(output_path=output_path)
    else:
        if page_url is None or page_number is None:
            raise HTTPException(status_code=400, detail="thieu page_url hoac page_number")
        crawler = {
            "batdongsan.com": crawl_batdongsan_listing_page,
            "nhatot.com": crawl_nhatot_listing_page,
        }[payload.source]
        rows = crawler(page_url, page_number=page_number, output_path=output_path)

    raise_if_stop_requested()
    normalized_rows = normalize_rows(rows, source=payload.source)
    save_rows(normalized_rows, output_path)

    return {
        "ok": True,
        "source": payload.source,
        "count": len(normalized_rows),
        "output": str(output_path),
    }


def _acquire_crawl_slot() -> None:
    if not CRAWL_RUN_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Dang co crawl job khac dang chay.")


def _release_crawl_slot() -> None:
    if CRAWL_RUN_LOCK.locked():
        CRAWL_RUN_LOCK.release()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(Path("web_ui.html"))


@app.get("/api/files/download")
def download_output_file(path: str) -> FileResponse:
    resolved = _resolve_output_path(path)
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Khong tim thay file output.")

    return FileResponse(
        resolved,
        media_type="text/csv",
        filename=resolved.name,
    )


@app.get("/api/browser/session")
def get_browser_session(request: Request) -> dict:
    return _browser_session_response(request)


@app.post("/api/browser/session/start")
def start_browser_session(payload: BrowserSessionStartRequest, request: Request) -> dict:
    return _start_browser_session(payload, request)


@app.post("/api/browser/session/complete")
def complete_browser_session(request: Request) -> dict:
    return _complete_browser_session(request)


@app.post("/api/browser/session/stop")
def stop_browser_session(request: Request) -> dict:
    return _stop_browser_session(request)


@app.post("/api/login/batdongsan/start")
def start_batdongsan_login(request: Request) -> dict:
    return _start_browser_session(BrowserSessionStartRequest(), request)


@app.post("/api/login/batdongsan/complete")
def complete_batdongsan_login(request: Request) -> dict:
    return _complete_browser_session(request)


@app.post("/api/crawl")
def run_crawl(payload: CrawlRequest) -> dict:
    _acquire_crawl_slot()
    try:
        return _execute_crawl(payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        _release_crawl_slot()


@app.post("/api/crawl/start", response_model=CrawlStartResponse)
def start_crawl(payload: CrawlRequest) -> dict:
    _validate_request(payload)
    _acquire_crawl_slot()
    try:
        job_id = uuid.uuid4().hex
        output_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        done_event = Event()
        stop_event = Event()
        job = {
            "queue": output_queue,
            "done": done_event,
            "stop": stop_event,
            "result": None,
            "error": None,
            "output": payload.output,
            "source": payload.source,
        }
        _store_job(job_id, job)

        def worker() -> None:
            writer = QueueTextWriter(output_queue)
            try:
                output_queue.put(("log", f"Khoi tao crawl job {job_id}"))
                with crawl_stop_context(stop_event.is_set), redirect_stdout(writer), redirect_stderr(writer):
                    result = _execute_crawl(payload)
                job["result"] = result
                output_queue.put(("done", result))
            except CrawlStoppedError as exc:
                partial = _build_partial_output(job.get("output"))
                stopped_result = {
                    "ok": True,
                    "status": "stopped",
                    "source": payload.source,
                    "message": str(exc),
                    "output": partial["output"] if partial else payload.output,
                    "count": partial["count"] if partial else 0,
                    "partial": partial,
                }
                job["result"] = stopped_result
                output_queue.put(("stopped", stopped_result))
            except Exception as exc:
                error_payload = {
                    "message": str(exc),
                    "source": payload.source,
                    "output": payload.output,
                    "partial": _build_partial_output(job.get("output")),
                }
                job["error"] = error_payload
                output_queue.put(("failed", error_payload))
            finally:
                writer.flush()
                done_event.set()
                _schedule_job_cleanup(job_id)
                _release_crawl_slot()

        threading.Thread(target=worker, daemon=True).start()
        return {"job_id": job_id}
    except Exception:
        _release_crawl_slot()
        raise


@app.post("/api/crawl/stop/{job_id}")
def stop_crawl(job_id: str) -> dict:
    job = _get_job(job_id)
    done_event: Event = job["done"]
    stop_event: Event = job["stop"]
    partial = _build_partial_output(job.get("output"))

    if done_event.is_set():
        return {
            "ok": True,
            "status": "done",
            "message": "Crawl job da ket thuc.",
            "partial": partial,
        }

    stop_event.set()
    return {
        "ok": True,
        "status": "stopping",
        "message": "Da gui yeu cau dung crawl. Job se dung sau buoc dang chay hien tai.",
        "partial": partial,
    }


@app.get("/api/crawl/events/{job_id}")
def crawl_events(job_id: str) -> StreamingResponse:
    job = _get_job(job_id)
    output_queue: queue.Queue[tuple[str, object]] = job["queue"]
    done_event: Event = job["done"]

    def event_stream():
        try:
            while True:
                try:
                    event_name, payload = output_queue.get(timeout=0.2)
                except queue.Empty:
                    if done_event.is_set():
                        break
                    continue

                yield _sse_event(event_name, payload)
                if event_name in {"done", "stopped", "failed"}:
                    break
        finally:
            if done_event.is_set():
                _remove_job(job_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
