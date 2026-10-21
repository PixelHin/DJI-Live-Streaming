#!/usr/bin/env python3
"""Start ZLMediaKit and the page server in this window, with tagged logs."""

import os
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MEDIA_DIR = ROOT / "ZLMediaKit" / "release" / "windows" / "Release"
MEDIA_EXE = MEDIA_DIR / "MediaServer.exe"
MEDIA_CONFIG = ROOT / "ZLMediaKit" / "conf" / "config.ini"
PAGE_SERVER = ROOT / "web" / "server.py"
WVP_DIR = ROOT / "wvp-GB28181-pro"
REDIS_DIR = ROOT / "tools" / "redis"
REDIS_EXE = REDIS_DIR / "redis-server.exe"
REDIS_CONF = REDIS_DIR / "redis.conf"

PRINT_LOCK = threading.Lock()


def emit(label, text):
    with PRINT_LOCK:
        print(f"[{label}] {text}", flush=True)


def decode_line(raw):
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding).rstrip("\r\n")
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").rstrip("\r\n")


def port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def pump(proc, label):
    stream = proc.stdout
    if stream is None:
        return
    for raw in iter(stream.readline, b""):
        text = decode_line(raw)
        if text:
            emit(label, text)


def stop_process(proc):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def start_process(args, cwd, env=None):
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        args,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )


def wait_for_port(port, proc, seconds=20):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        if port_open(port):
            return True
        time.sleep(0.2)
    return port_open(port)


def find_wvp_jar():
    jars = [
        path for path in (WVP_DIR / "target").glob("wvp-pro-*.jar")
        if "sources" not in path.name and "javadoc" not in path.name
    ]
    if not jars:
        return None
    return max(jars, key=lambda path: path.name)


def already_running():
    page_up = port_open(8765)
    media_up = port_open(8080) or port_open(1935)
    sip_up = port_open(18080) or port_open(8116)
    if page_up:
        emit("start_server", "The page server is already running on port 8765. Stop that window first.")
    if media_up:
        emit("start_server", "ZLMediaKit is already running. Stop MediaServer.exe first.")
    if sip_up:
        emit("start_server", "The SIP server is already running. Stop wvp first.")
    return page_up or media_up or sip_up


def main():
    if not MEDIA_EXE.is_file():
        emit("start_server", f"MediaServer.exe was not found at {MEDIA_EXE}")
        return 1
    if not MEDIA_CONFIG.is_file():
        emit("start_server", f"ZLMediaKit config was not found at {MEDIA_CONFIG}")
        return 1
    if not PAGE_SERVER.is_file():
        emit("start_server", f"server.py was not found at {PAGE_SERVER}")
        return 1
    wvp_jar = find_wvp_jar()
    if wvp_jar is None:
        emit("start_server", "The wvp jar was not found. Build it with mvn -DskipTests package in wvp-GB28181-pro.")
        return 1
    if not REDIS_EXE.is_file():
        emit("start_server", f"redis-server.exe was not found at {REDIS_EXE}")
        return 1
    if already_running():
        return 1

    emit("start_server", "Starting Redis, ZLMediaKit, the SIP server, and the page server.")
    started_redis = False
    redis = None
    if port_open(6379):
        emit("start_server", "Redis is already running on port 6379.")
    else:
        redis = start_process([str(REDIS_EXE), str(REDIS_CONF)], REDIS_DIR)
        threading.Thread(target=pump, args=(redis, "redis"), name="redis-log", daemon=True).start()
        started_redis = True
        if not wait_for_port(6379, redis, seconds=15):
            emit("start_server", "Redis did not open port 6379.")
            stop_process(redis)
            return 1

    media = start_process(
        [str(MEDIA_EXE), "-c", str(MEDIA_CONFIG)],
        MEDIA_DIR,
    )
    threading.Thread(target=pump, args=(media, "ZLMediaKit"), name="zlm-log", daemon=True).start()
    if not wait_for_port(8080, media):
        emit("start_server", "ZLMediaKit did not open port 8080.")
        stop_process(media)
        if started_redis:
            stop_process(redis)
        return 1

    wvp = start_process(
        [
            "java", "--enable-native-access=ALL-UNNAMED", "-jar", str(wvp_jar),
            "--spring.profiles.active=local",
            "--spring.config.additional-location=file:./src/main/resources/",
        ],
        WVP_DIR,
    )
    threading.Thread(target=pump, args=(wvp, "wvp"), name="wvp-log", daemon=True).start()
    if not wait_for_port(18080, wvp, seconds=180):
        emit("start_server", "The SIP server did not open port 18080.")
        stop_process(wvp)
        stop_process(media)
        if started_redis:
            stop_process(redis)
        return 1

    page_env = os.environ.copy()
    page_env["PYTHONUNBUFFERED"] = "1"
    page_env["STREAM_LAUNCHED"] = "1"
    page = start_process(
        [sys.executable, "-u", str(PAGE_SERVER)],
        ROOT / "web",
        page_env,
    )
    threading.Thread(target=pump, args=(page, "server.py"), name="page-log", daemon=True).start()

    commands = queue.Queue()

    def read_commands():
        while True:
            try:
                commands.put(input())
            except (EOFError, KeyboardInterrupt):
                commands.put("stop")
                return

    threading.Thread(target=read_commands, name="input", daemon=True).start()
    emit("start_server", "Open http://127.0.0.1:8765")
    emit("start_server", "GB28181 page: http://127.0.0.1:8765/gb28181.html")
    emit("start_server", "Type stop to exit.")

    exit_code = 0
    try:
        while True:
            if media.poll() is not None:
                emit("start_server", f"ZLMediaKit exited ({media.returncode}).")
                exit_code = 1
                break
            if wvp.poll() is not None:
                emit("start_server", f"The SIP server exited ({wvp.returncode}).")
                exit_code = 1
                break
            if page.poll() is not None:
                emit("start_server", f"server.py exited ({page.returncode}).")
                exit_code = 1
                break
            if started_redis and redis.poll() is not None:
                emit("start_server", f"Redis exited ({redis.returncode}).")
                exit_code = 1
                break
            try:
                command = commands.get(timeout=0.3)
            except queue.Empty:
                continue
            if command.strip().lower() == "stop":
                emit("start_server", "stop received, shutting down")
                break
            if command.strip():
                emit("start_server", "Unknown command. Type stop to exit.")
    except KeyboardInterrupt:
        emit("start_server", "Shutting down.")
    finally:
        stop_process(page)
        stop_process(wvp)
        stop_process(media)
        if started_redis:
            stop_process(redis)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
