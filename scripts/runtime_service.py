"""Single existing-environment service entry. No dependency installation/reindex."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "run"
PROFILE = ROOT / "deploy/runtime-profile.json"


def load_profile():
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    if profile.get("version") != "sai-runtime/1":
        raise ValueError("不支持的部署配置版本")
    required = ["qdrant_binary", "storage", "bm25_vocab", "prompt", "active_documents"]
    for key in required:
        path = (ROOT / profile[key]).resolve()
        if not path.exists():
            raise FileNotFoundError(path)
    return profile


def environment(profile):
    env = dict(os.environ, **profile["environment"])
    env.update(QDRANT_COLLECTION=profile["collection"],
               BM25_VOCAB_PATH=str((ROOT / profile["bm25_vocab"]).resolve()),
               SYSTEM_PROMPT_PATH=str((ROOT / profile["prompt"]).resolve()),
               ACTIVE_DOCUMENTS_PATH=str((ROOT / profile["active_documents"]).resolve()),
               SAI_RUNTIME_PROFILE=str(PROFILE))
    env["PATH"] = str(Path(profile["python"]).parent) + ":/usr/local/cuda/bin:/usr/sbin:/usr/bin:/bin"
    return env


def commands(profile):
    py = profile["python"]
    return {
        "qdrant": ([str((ROOT / profile["qdrant_binary"]).resolve())], ROOT, 6333),
        "backend": ([py, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "6006"], ROOT / "backend", 6006),
    }


def state():
    path = STATE / "processes.json"
    return json.loads(path.read_text()) if path.exists() else {}


def matches_process(row):
    path = Path(f"/proc/{row['pid']}/cmdline")
    return path.exists() and path.read_bytes().split(b"\0")[:-1] == [x.encode() for x in row["args"]]


def start(name, profile):
    args, cwd, port = commands(profile)[name]
    old = state()
    if name in old and matches_process(old[name]):
        print(name, "already running", old[name]["pid"])
        return
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"端口{port}被未登记进程占用，不擅自停止")
    STATE.mkdir(parents=True, exist_ok=True)
    env = environment(profile)
    if name == "qdrant":
        env.update(QDRANT__STORAGE__STORAGE_PATH=str((ROOT / profile["storage"]).resolve()),
                   QDRANT__STORAGE__SNAPSHOTS_PATH=str((ROOT / profile["snapshots"]).resolve()),
                   QDRANT__SERVICE__HOST="127.0.0.1", QDRANT__SERVICE__HTTP_PORT="6333",
                   QDRANT__SERVICE__GRPC_PORT="6334")
    with (STATE / (name + ".log")).open("ab") as log:
        process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    old[name] = {"pid": process.pid, "args": args,
                 "profile_sha256": hashlib.sha256(PROFILE.read_bytes()).hexdigest()}
    (STATE / "processes.json").write_text(json.dumps(old, indent=2))
    print(name, "started", process.pid, flush=True)


def stop(name):
    rows = state()
    row = rows.get(name)
    if row and matches_process(row):
        os.killpg(row["pid"], signal.SIGTERM)
        for _ in range(50):
            if not matches_process(row):
                break
            time.sleep(0.2)
        if matches_process(row):
            raise RuntimeError(f"{name}未按期退出，请检查进程，不宽泛pkill")
    rows.pop(name, None)
    if STATE.exists():
        (STATE / "processes.json").write_text(json.dumps(rows, indent=2))


def wait_ready(name):
    url = {"qdrant": "http://127.0.0.1:6333/collections", "backend": "http://127.0.0.1:6006/api/health"}[name]
    for _ in range(240):
        row = state().get(name)
        if not row or not matches_process(row):
            raise RuntimeError(f"{name}启动失败，请查看专用日志")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise TimeoutError(name + " readiness")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument("service", choices=("all", "qdrant", "backend"), default="all", nargs="?")
    args = parser.parse_args()
    profile = load_profile()
    services = ("qdrant", "backend") if args.service == "all" else (args.service,)
    for name in reversed(services) if args.action == "stop" else services:
        if args.action == "start":
            start(name, profile)
            wait_ready(name)
        elif args.action == "stop":
            stop(name)
        else:
            print(name, bool(state().get(name) and matches_process(state()[name])))
