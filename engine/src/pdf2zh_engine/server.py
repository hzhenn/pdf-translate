from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import traceback
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from pdf2zh_engine.job import EngineJob


class JobState:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.done = False
        self.result: dict[str, Any] | None = None
        self.error: dict[str, Any] | None = None
        self.cv = threading.Condition()


class EngineService:
    def __init__(self) -> None:
        self.jobs: dict[str, JobState] = {}
        self.lock = threading.Lock()

    def create_job(self) -> tuple[str, JobState]:
        job_id = str(uuid.uuid4())
        state = JobState()
        with self.lock:
            self.jobs[job_id] = state
        return job_id, state

    def get_job(self, job_id: str) -> JobState | None:
        with self.lock:
            return self.jobs.get(job_id)


SERVICE = EngineService()


def _emit(state: JobState, payload: dict[str, Any]) -> None:
    with state.cv:
        state.events.append(payload)
        state.cv.notify_all()


def _event_to_progress(event: dict[str, Any]) -> dict[str, Any] | None:
    pct: float | None = None
    for key in ("overall_progress", "stage_progress", "progress"):
        if key in event:
            try:
                value = float(event[key])
            except Exception:
                continue
            pct = value * 100 if value <= 1 else value
            break

    if pct is None and event.get("type") in ("start", "engine_start"):
        pct = 0

    if pct is None:
        return None

    stage = str(event.get("stage") or event.get("type") or "progress")
    message = str(event.get("message") or "")
    return {
        "type": "progress",
        "pct": max(0, min(100, round(pct))),
        "stage": stage,
        "message": message,
    }


def _resolve_source(payload: dict[str, Any]) -> tuple[str, str | None]:
    source_path = payload.get("source_path") or payload.get("sourcePath")
    if not source_path and isinstance(payload.get("inputs"), list):
        inputs = payload.get("inputs")
        if inputs:
            source_path = inputs[0]
    if not source_path:
        raise ValueError("Missing source_path")
    source_filename = payload.get("source_filename") or payload.get("sourceFilename")
    return str(source_path), (str(source_filename) if source_filename else None)


def _build_job(payload: dict[str, Any], output_dir: str) -> tuple[EngineJob, str]:
    source_path, source_filename = _resolve_source(payload)
    service = payload.get("service", "google")
    threads = 4
    lang_in = payload.get("lang_in", "en")
    lang_out = payload.get("lang_out", "zh")

    job = EngineJob.model_validate(
        {
            "inputs": [source_path],
            "outputDir": output_dir,
            "service": service,
            "langIn": lang_in,
            "langOut": lang_out,
            "dual": True,
            "mono": False,
            "qps": 4,
            "reportInterval": 1,
            "threads": int(threads),
        }
    )

    if not source_filename:
        source_filename = Path(source_path).name
    return job, source_filename


def _find_output_pdf(output_dir: str) -> Path:
    candidates = list(Path(output_dir).rglob("*.pdf"))
    if not candidates:
        raise FileNotFoundError("No PDF output found in temporary directory")
    dual_candidates = [p for p in candidates if "dual" in p.name.lower()]
    ordered = dual_candidates or candidates
    return max(ordered, key=lambda p: p.stat().st_mtime)


def _run_job(state: JobState, payload: dict[str, Any]) -> None:
    temp_dir: str | None = None
    try:
        temp_dir = tempfile.mkdtemp(prefix="pdf2zh-engine-")
        job, source_filename = _build_job(payload, temp_dir)

        def emit(event: dict[str, Any]) -> None:
            progress = _event_to_progress(event)
            if progress:
                _emit(state, progress)
            if event.get("type") == "finish":
                _emit(state, {"type": "done", "pct": 100})

        stdout = sys.stdout
        sys.stdout = sys.stderr
        try:
            import asyncio
            from pdf2zh_engine.runner import run_job_stream

            asyncio.run(run_job_stream(job, emit))
        finally:
            sys.stdout = stdout

        output_pdf = _find_output_pdf(temp_dir)
        pdf_bytes = output_pdf.read_bytes()
        pdf_base64 = base64.b64encode(pdf_bytes).decode("ascii")
        stem = Path(source_filename).stem if source_filename else "output"
        state.result = {
            "ok": True,
            "filename": f"{stem} (双语).pdf",
            "pdf_base64": pdf_base64,
        }
        _emit(state, {"type": "done", "pct": 100})
        state.done = True
    except Exception as exc:
        detail = traceback.format_exc()
        sys.stderr.write(detail + "\n")
        sys.stderr.flush()
        state.error = {"ok": False, "error": str(exc), "detail": detail}
        _emit(state, {"type": "error", "message": str(exc), "detail": detail})
        state.done = True
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        with state.cv:
            state.cv.notify_all()


class Handler(BaseHTTPRequestHandler):
    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        if not raw:
            return {}
        return json.loads(raw)

    def do_POST(self) -> None:
        if self.path != "/translate":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json()
        except Exception as exc:
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if "source_path" not in payload and "sourcePath" not in payload:
            self._json_response(
                HTTPStatus.BAD_REQUEST, {"error": "source_path required"}
            )
            return

        service = payload.get("service")
        if service not in {"google", "bing"}:
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": f"Unsupported service: {service}"},
            )
            return

        job_id, state = SERVICE.create_job()
        thread = threading.Thread(target=_run_job, args=(state, payload), daemon=True)
        thread.start()
        self._json_response(HTTPStatus.OK, {"jobId": job_id})

    def do_GET(self) -> None:
        if self.path.startswith("/events"):
            job_id = self._query_param("jobId")
            if not job_id:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            state = SERVICE.get_job(job_id)
            if not state:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self._stream_events(state)
            return

        if self.path.startswith("/result"):
            job_id = self._query_param("jobId")
            if not job_id:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            state = SERVICE.get_job(job_id)
            if not state:
                self._json_response(
                    HTTPStatus.NOT_FOUND, {"ok": False, "error": "job not found"}
                )
                return
            if state.result:
                self._json_response(HTTPStatus.OK, state.result)
                return
            if state.error:
                self._json_response(HTTPStatus.OK, state.error)
                return
            self._json_response(
                HTTPStatus.OK, {"ok": False, "error": "job not finished"}
            )
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def _query_param(self, key: str) -> str | None:
        if "?" not in self.path:
            return None
        query = self.path.split("?", 1)[1]
        for part in query.split("&"):
            if not part:
                continue
            k, _, v = part.partition("=")
            if k == key:
                return v
        return None

    def _stream_events(self, state: JobState) -> None:
        try:
            while True:
                event: dict[str, Any] | None = None
                with state.cv:
                    if state.events:
                        event = state.events.pop(0)
                    elif state.done:
                        break
                    else:
                        state.cv.wait(timeout=1)
                if event is None:
                    continue
                data = json.dumps(event, ensure_ascii=True)
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
                if event.get("type") in ("done", "error"):
                    break
        except BrokenPipeError:
            return

    def log_message(self, format: str, *args: Any) -> None:
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pdf2zh-engine-server")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    port = httpd.server_address[1]
    sys.stdout.write(json.dumps({"type": "ready", "port": port}) + "\n")
    sys.stdout.flush()
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
