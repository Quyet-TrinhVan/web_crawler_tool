import json
import queue
import threading
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from threading import Event, Lock

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from core.crawl_batdongsan_list import (
    crawl_categories_for_today as crawl_batdongsan_categories_for_today,
    crawl_listing_page as crawl_batdongsan_listing_page,
)
from core.crawl_nhatot_list import crawl_listing_page as crawl_nhatot_listing_page
from core.login_batdongsan import HOME_URL, USER_DATA_DIR, build_driver as build_batdongsan_login_driver
from main import normalize_rows, save_rows


app = FastAPI(title="Web Crawler UI")
LOGIN_DRIVER = None
LOGIN_LOCK = Lock()
JOB_LOCK = Lock()
CRAWL_JOBS: dict[str, dict] = {}


class CrawlRequest(BaseModel):
    source: str
    output: str
    page_url: str | None = None
    page_number: int | None = None
    date: str | None = None


class CrawlStartResponse(BaseModel):
    job_id: str


class QueueTextWriter:
    def __init__(self, output_queue: queue.Queue[tuple[str, str]]):
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


def _validate_request(payload: CrawlRequest) -> None:
    valid_sources = {"batdongsan.com", "nhatot.com"}
    if payload.source not in valid_sources:
        raise HTTPException(status_code=400, detail="source khong hop le")

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

    normalized_rows = normalize_rows(rows, source=payload.source)
    save_rows(normalized_rows, output_path)

    return {
        "ok": True,
        "source": payload.source,
        "count": len(normalized_rows),
        "output": str(output_path),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(Path("web_ui.html"))


@app.post("/api/login/batdongsan/start")
def start_batdongsan_login() -> dict:
    global LOGIN_DRIVER

    with LOGIN_LOCK:
        if LOGIN_DRIVER is not None:
            raise HTTPException(
                status_code=409,
                detail="Dang co mot phien login Batdongsan dang mo. Hay bam 'Da login xong' truoc.",
            )

        try:
            LOGIN_DRIVER = build_batdongsan_login_driver()
            LOGIN_DRIVER.get(HOME_URL)
        except Exception as exc:
            LOGIN_DRIVER = None
            raise HTTPException(status_code=500, detail=f"Khong mo duoc Chrome login: {exc}") from exc

    return {
        "ok": True,
        "message": "Chrome da mo voi profile Batdongsan. Hay dang nhap/xac minh trong cua so Chrome.",
        "profile": str(USER_DATA_DIR),
    }


@app.post("/api/login/batdongsan/complete")
def complete_batdongsan_login() -> dict:
    global LOGIN_DRIVER

    with LOGIN_LOCK:
        if LOGIN_DRIVER is None:
            raise HTTPException(status_code=400, detail="Khong co phien login nao dang mo.")

        try:
            LOGIN_DRIVER.quit()
        except Exception as exc:
            LOGIN_DRIVER = None
            raise HTTPException(status_code=500, detail=f"Khong dong duoc Chrome login: {exc}") from exc

        LOGIN_DRIVER = None

    return {
        "ok": True,
        "message": "Da dong Chrome login va luu session Batdongsan.",
        "profile": str(USER_DATA_DIR),
    }


@app.post("/api/crawl")
def run_crawl(payload: CrawlRequest) -> dict:
    try:
        return _execute_crawl(payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/crawl/start", response_model=CrawlStartResponse)
def start_crawl(payload: CrawlRequest) -> dict:
    _validate_request(payload)

    job_id = uuid.uuid4().hex
    output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    done_event = Event()
    job = {
        "queue": output_queue,
        "done": done_event,
        "result": None,
        "error": None,
    }
    _store_job(job_id, job)

    def worker() -> None:
        writer = QueueTextWriter(output_queue)
        try:
            output_queue.put(("log", f"Khoi tao crawl job {job_id}"))
            with redirect_stdout(writer), redirect_stderr(writer):
                result = _execute_crawl(payload)
            job["result"] = result
            output_queue.put(("done", json.dumps(result, ensure_ascii=False)))
        except Exception as exc:
            message = str(exc)
            job["error"] = message
            output_queue.put(("error", message))
        finally:
            writer.flush()
            done_event.set()

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/crawl/events/{job_id}")
def crawl_events(job_id: str) -> StreamingResponse:
    job = _get_job(job_id)
    output_queue: queue.Queue[tuple[str, str]] = job["queue"]
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
                if event_name in {"done", "error"}:
                    break
        finally:
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
