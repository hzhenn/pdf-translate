# pdf2zh-engine (Engine v1)

Standalone server for Electron:

```
python -m pdf2zh_engine.server --port 0
```

## Local run

From repo root:

```bash
python3 -m venv engine/.venv
source engine/.venv/bin/activate
python -m pip install -U pip
python -m pip install -e engine
python -m pdf2zh_engine.server --port 0
```

## Server protocol

- STDOUT first line: {"type":"ready","port":12345}
- STDERR: progress/error logs and tracebacks
- POST /translate -> {jobId}
- GET /events?jobId=... -> SSE progress/done/error
- GET /result?jobId=... -> {ok, filename, pdf_base64}

## Build on macOS / Windows 11

From repo root:

```bash
npm run engine:build:mac
```

```powershell
npm run engine:build:win
```

Binary output:

```
dist/pdf2zh-engine
dist/pdf2zh-engine.exe
```
