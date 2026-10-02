#!/usr/bin/env python3
"""Local page server. Forwards livestream start to DJI FlightHub."""

import json
import logging
import os
import re
import threading
import uuid
from collections import deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = "https://es-flight-api-cn.djigate.com"
log = logging.getLogger("viewer")
ZLM_API = "http://127.0.0.1:8080"
ZLM_SECRET = "Bv9cjFxBjsZ19yH1MWkmpRyEglHTP1gc"
ZLM_HTTP_PORT = "8080"
LOG_LINES = deque(maxlen=500)


class MemoryLogHandler(logging.Handler):
    def emit(self, record):
        LOG_LINES.append(self.format(record))


def path_name(value):
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", value or "")
    return cleaned.strip("_") or "live"


def zlm_api():
    return os.environ.get("ZLM_API", ZLM_API).rstrip("/")


def zlm_secret():
    return os.environ.get("ZLM_SECRET", ZLM_SECRET)


def zlm_call(path, params, required=True):
    query = dict(params)
    query["secret"] = zlm_secret()
    request = Request(zlm_api() + path + "?" + urlencode(query))
    try:
        with urlopen(request, timeout=12) as response:
            data = json.loads(response.read().decode() or "{}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        raise RuntimeError(detail or "ZLMediaKit rejected the stream (" + str(exc.code) + ").") from exc
    except TimeoutError as exc:
        raise RuntimeError("The stream source did not answer in time.") from exc
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise RuntimeError("The stream source did not answer in time.") from exc
        raise RuntimeError("ZLMediaKit is not running. Start MediaServer from the ZLMediaKit folder so it can listen on ports 1935, 554, and 8080.") from exc
    code = data.get("code", 0)
    message = str(data.get("msg") or "")
    if code not in (0, "0") and required and "exist" not in message.lower():
        raise RuntimeError(message or "ZLMediaKit rejected the stream.")
    return data


def stream_names(url, sn):
    parts = [part for part in (url or "").split("/") if part]
    if len(parts) >= 2:
        return path_name(parts[0]), path_name(parts[-1])
    if parts:
        return "live", path_name(parts[-1])
    if sn:
        return "live", path_name(sn)
    raise ValueError("Enter a full rtmp:// or rtsp:// link with a stream name, or a device SN.")


def prepare_stream(protocol, url, sn, page_host):
    if protocol == "gb28181":
        if not (sn or "").strip():
            raise ValueError("Enter the GB28181 stream id.")
        stream = path_name(sn)
        data = zlm_call("/index/api/openRtpServer", {
            "port": "0",
            "tcp_mode": "0",
            "stream_id": stream,
            "app": "rtp",
        })
        return {
            "app": "rtp",
            "stream": stream,
            "source": "gb28181",
            "rtp_port": data.get("port"),
        }
    parsed = urlparse((url or "").strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("rtmp", "rtmps", "rtsp", "rtsps"):
        scheme = "rtmp" if protocol == "rtmp" else "rtsp"
        parsed = urlparse(scheme + "://" + (url or "").strip().lstrip("/"))
    stream_path = parsed.path.strip("/")
    if not stream_path and sn:
        parsed = parsed._replace(path="/" + sn)
        stream_path = sn
    if not parsed.hostname:
        raise ValueError("Enter a full rtmp:// or rtsp:// link.")
    if not stream_path:
        app, stream = "live", path_name(parsed.hostname)
        if not parsed.path:
            parsed = parsed._replace(path="/")
    else:
        app, stream = stream_names(stream_path, sn)
    local_names = {"localhost", "127.0.0.1", "::1", (page_host or "").split(":")[0].lower()}
    if (parsed.hostname or "").lower() in local_names:
        return {"app": app, "stream": stream, "source": "publisher"}
    source = urlunparse(parsed)
    zlm_call("/index/api/delStreamProxy", {"key": "__defaultVhost__/" + app + "/" + stream}, required=False)
    zlm_call("/index/api/addStreamProxy", {
        "vhost": "__defaultVhost__",
        "app": app,
        "stream": stream,
        "url": source,
        "retry_count": "-1",
        "enable_rtsp": "1",
        "enable_rtmp": "1",
        "rtp_type": "0",
        "timeout_sec": "8",
    })
    return {"app": app, "stream": stream, "source": "pull"}


def stream_protocol(url, url_type=""):
    """Classify a playback link as webrtc, rtmp, rtsp, agora, or volc."""
    hinted = (url_type or "").strip().lower()
    text = url or ""
    if hinted in ("volc", "volcengine", "volcano"):
        return "volc"
    if "room_id=" in text or "roomId=" in text:
        return "volc"
    if hinted == "agora" or ("channel=" in text and "token=" in text):
        return "agora"
    parsed = urlparse(text.strip())
    scheme = (parsed.scheme or "").lower()
    path = (parsed.path or "").lower()
    if scheme in ("rtmp", "rtmps"):
        return "rtmp"
    if scheme in ("rtsp", "rtsps"):
        return "rtsp"
    if scheme in ("whep", "wheps", "whip", "webrtc"):
        return "webrtc"
    if "/whep" in path or "/whip" in path or path.startswith("/rtc/"):
        return "webrtc"
    if hinted in ("rtmp", "rtsp", "webrtc"):
        return hinted
    if hinted == "srs":
        return "webrtc"
    return "unknown"


def load_env(path):
    if not os.path.exists(path):
        log.info("no .env file at %s", path)
        return
    loaded = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
            loaded += 1
    log.info("loaded %s entries from .env", loaded)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def log_message(self, fmt, *args):
        if self.path.split("?", 1)[0] == "/api/logs":
            return
        log.info("%s %s", self.address_string(), fmt % args)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/api/logs":
            self._json(200, {"lines": list(LOG_LINES)})
            return
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/logs":
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode() or "{}")
            except json.JSONDecodeError:
                self._json(400, {"code": -1, "message": "Invalid JSON"})
                return
            message = str(body.get("message") or "").strip()
            if not message:
                self._json(400, {"code": -1, "message": "Empty log message"})
                return
            log.info("page: %s", message[:300])
            self._json(200, {"code": 0})
            return
        if path == "/api/zlm/prepare":
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode() or "{}")
            except json.JSONDecodeError:
                self._json(400, {"code": -1, "message": "Invalid JSON"})
                return
            protocol = str(body.get("protocol") or "").strip().lower()
            link = str(body.get("url") or "").strip()
            sn = str(body.get("sn") or "").strip()
            page_host = (self.headers.get("Host") or "127.0.0.1").split(":")[0]
            try:
                prepared = prepare_stream(protocol, link, sn, page_host)
            except ValueError as exc:
                self._json(400, {"code": -1, "message": str(exc)})
                return
            except RuntimeError as exc:
                log.info("ZLMediaKit prepare failed: %s", exc)
                self._json(502, {"code": -1, "message": str(exc)})
                return
            http_port = os.environ.get("ZLM_HTTP_PORT", ZLM_HTTP_PORT)
            whep = "http://" + page_host + ":" + http_port + "/index/api/whep?app=" + prepared["app"] + "&stream=" + prepared["stream"]
            log.info(
                "ZLMediaKit app=%s stream=%s source=%s playback=%s",
                prepared["app"],
                prepared["stream"],
                prepared["source"],
                whep,
            )
            self._json(200, {
                "code": 0,
                "app": prepared["app"],
                "stream": prepared["stream"],
                "whep": whep,
                "rtp_port": prepared.get("rtp_port"),
                "source": prepared["source"],
            })
            return
        if path != "/api/live/start":
            log.info("%s POST %s rejected", self.address_string(), path)
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            log.info("%s POST /api/live/start rejected: invalid JSON", self.address_string())
            self._json(400, {"code": -1, "message": "Invalid JSON"})
            return

        token = str(body.get("token") or os.environ.get("FLIGHTHUB_TOKEN") or "").strip()
        project = str(body.get("project_uuid") or os.environ.get("FLIGHTHUB_PROJECT_UUID") or "").strip()
        sn = str(body.get("sn") or "").strip()
        camera = str(body.get("camera_index") or "").strip()
        token_source = "request" if str(body.get("token") or "").strip() else "environment"
        log.info(
            "%s start livestream sn=%s camera=%s token=%s project=%s",
            self.address_string(),
            sn or "-",
            camera or "-",
            "present (" + token_source + ")" if token else "missing",
            "present" if project else "missing",
        )
        if not token or not project:
            self._json(400, {"code": -1, "message": "Enter the FlightHub user token and project UUID."})
            return
        if not sn or not camera:
            log.info("start livestream rejected: device SN or camera index is empty")
            self._json(400, {"code": -1, "message": "Enter the device SN and camera index."})
            return

        payload = {
            "sn": sn,
            "camera_index": camera,
            "video_expire": int(body.get("video_expire") or 7200),
            "quality_type": body.get("quality_type") or "adaptive",
        }
        request_id = str(uuid.uuid4())
        target = os.environ.get("FLIGHTHUB_BASE", BASE).rstrip("/") + "/openapi/v2.0/live-stream/start"
        log.info("calling FlightHub %s request_id=%s", target, request_id)
        request = Request(
            target,
            data=json.dumps(payload).encode(),
            headers={
                "X-Request-Id": request_id,
                "X-Language": "zh",
                "X-Project-Uuid": project,
                "X-User-Token": token,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                data = response.read()
                status = response.status
        except HTTPError as exc:
            data = exc.read()
            status = exc.code
            log.info("FlightHub returned HTTP %s", status)
        except URLError as exc:
            log.info("FlightHub request failed: %s", exc.reason)
            self._json(502, {"code": -1, "message": "Could not reach FlightHub: " + str(exc.reason)})
            return
        parsed = None
        try:
            parsed = json.loads(data.decode())
        except json.JSONDecodeError:
            preview = data.decode("utf-8", "replace").lstrip()[:40].replace("\n", " ")
            log.info("FlightHub returned HTTP %s body is not JSON (%s)", status, preview)
            self._json(502, {
                "code": -1,
                "message": "FlightHub returned a web page instead of JSON. Check the API address and token.",
            })
            return
        payload = parsed.get("data") or {}
        url_type = str(payload.get("url_type") or "-")
        protocol = stream_protocol(payload.get("url") or "", url_type)
        log.info(
            "FlightHub returned HTTP %s code=%s message=%s url_type=%s protocol=%s",
            status,
            parsed.get("code", "-"),
            parsed.get("message") or "-",
            url_type,
            protocol,
        )
        raw = json.dumps(parsed).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, status, obj):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def wait_for_stop():
    if os.environ.get("STREAM_LAUNCHED") == "1":
        log.info("running under start_server; type stop in that window to exit")
        threading.Event().wait()
        return
    log.info("type stop to exit")
    while True:
        try:
            command = input("> ")
        except EOFError:
            log.info("no console attached; the page server keeps running")
            threading.Event().wait()
            return
        except KeyboardInterrupt:
            log.info("input closed, shutting down")
            return
        if command.strip().lower() == "stop":
            log.info("stop received, shutting down")
            return
        if command.strip():
            log.info("unknown command %r; type stop to exit", command.strip())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    memory = MemoryLogHandler()
    memory.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    logging.getLogger().addHandler(memory)
    load_env(os.path.join(ROOT, ".env"))
    server = ThreadingHTTPServer(("0.0.0.0", 8765), Handler)
    worker = threading.Thread(target=server.serve_forever, name="http", daemon=True)
    worker.start()
    log.info("listening on http://0.0.0.0:8765")
    try:
        wait_for_stop()
    finally:
        server.shutdown()
        server.server_close()
        log.info("server stopped")
