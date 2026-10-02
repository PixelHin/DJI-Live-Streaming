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


def already_running():
    page_up = port_open(8765)
    media_up = port_open(8080) or port_open(1935)
    if page_up:
        emit("start_server", "The page server is already running on port 8765. Stop that window first.")
    if media_up:
        emit("start_server", "ZLMediaKit is already running. Stop MediaServer.exe first.")
    return page_up or media_up


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
    if already_running():
        return 1

    emit("start_server", "Starting ZLMediaKit and the page server.")
    media = start_process(
        [str(MEDIA_EXE), "-c", str(MEDIA_CONFIG)],
        MEDIA_DIR,
    )
    threading.Thread(target=pump, args=(media, "ZLMediaKit"), name="zlm-log", daemon=True).start()
    if not wait_for_port(8080, media):
        emit("start_server", "ZLMediaKit did not open port 8080.")
        stop_process(media)
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
    emit("start_server", "Type stop to exit both.")

    exit_code = 0
    try:
        while True:
            if media.poll() is not None:
                emit("start_server", f"ZLMediaKit exited ({media.returncode}).")
                exit_code = 1
                break
            if page.poll() is not None:
                emit("start_server", f"server.py exited ({page.returncode}).")
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
                emit("start_server", "Unknown command. Type stop to exit both.")
    except KeyboardInterrupt:
        emit("start_server", "Shutting down.")
    finally:
        stop_process(page)
        stop_process(media)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
