"""Task-scoped Jupyter client; never interrupts or shuts down a board kernel.

Password is read from stdin and kept in memory. Requires requests and
websocket-client in the active environment. The board URL is explicit.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote, urlparse
import uuid

def save_report(path, report):
    """Preserve the last complete report during transient Windows file locks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending-" + uuid.uuid4().hex)
    with pending.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=True)
    for attempt in range(5):
        try:
            os.replace(pending, path)
            return
        except PermissionError:
            if attempt == 4:
                raise  # Keep the complete pending report; never replay a request.
            time.sleep(0.1)


def main():
    import requests
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "run", "upload", "download"))
    parser.add_argument("--base-url", required=True, help="Confirmed direct-connected board HTTP origin")
    parser.add_argument("--kernel")
    parser.add_argument("--code", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--remote")
    parser.add_argument("--sha256")
    args = parser.parse_args()
    # Submission derivative: no deployment address is embedded in this source.
    base = args.base_url.rstrip("/")
    origin = urlparse(base)
    if (origin.scheme != "http" or not origin.hostname or origin.path or
            origin.query or origin.fragment or origin.username or origin.password):
        parser.error("--base-url must be a plain HTTP origin without credentials")
    session = requests.Session()
    session.trust_env = False
    session.get(base + "/login", timeout=10).raise_for_status()
    xsrf = session.cookies.get("_xsrf")
    password = sys.stdin.readline().rstrip("\r\n")
    session.post(base + "/login", data={"_xsrf": xsrf, "password": password},
                 timeout=10).raise_for_status()
    password = None
    session.headers.update({"X-XSRFToken": xsrf, "Referer": base + "/tree"})

    def api(method, path, **kwargs):
        response = session.request(method, base + "/api/" + path,
                                   timeout=15, allow_redirects=False, **kwargs)
        response.raise_for_status()
        if "application/json" not in response.headers.get("Content-Type", ""):
            raise RuntimeError("Jupyter authentication/API response not confirmed")
        return response.json()

    report = {"local_utc": datetime.now(timezone.utc).isoformat(), "action": args.action}

    def save():
        save_report(args.output, report)

    # Authentication must be established before any mutation.
    kernels = api("GET", "kernels")
    if args.action == "status":
        report.update(kernels=kernels, sessions=api("GET", "sessions"))
    elif args.action == "download":
        if not args.file or not args.remote or not args.sha256:
            parser.error("download requires --file, --remote and --sha256")
        if args.file.exists():
            raise FileExistsError("Download destination already exists")
        downloaded = api("GET", "contents/" + quote(args.remote, safe="/"),
                         params={"format": "base64", "content": 1})
        if downloaded.get("type") != "file" or downloaded.get("format") != "base64":
            raise RuntimeError("Expected a base64 file response")
        payload = base64.b64decode(downloaded["content"], validate=False)
        checksum = hashlib.sha256(payload).hexdigest()
        if checksum != args.sha256:
            raise ValueError("Downloaded file checksum mismatch")
        with args.file.open("xb") as output:
            output.write(payload)
        report["downloaded"] = {"path": args.remote, "bytes": len(payload), "sha256": checksum}
    elif args.action == "upload":
        if not args.file or not args.remote:
            parser.error("upload requires --file and --remote")
        path = "contents/" + quote(args.remote, safe="/")
        existing = session.get(base + "/api/" + path, timeout=10)
        if existing.status_code != 404:
            raise RuntimeError("Upload destination already exists or cannot be checked")
        uploaded = api("PUT", path, json={"type": "file", "format": "base64",
                       "content": base64.b64encode(args.file.read_bytes()).decode("ascii")})
        report["uploaded"] = {key: uploaded.get(key) for key in ("path", "size", "type")}
    else:
        import websocket
        if not args.code:
            parser.error("run requires --code")
        code = args.code.read_text(encoding="utf-8")
        if args.kernel:
            if args.kernel not in [kernel["id"] for kernel in kernels]:
                raise RuntimeError("Requested persistent kernel is no longer running")
            kernel_id = args.kernel
        else:
            kernel_id = api("POST", "kernels", json={"name": "python3"})["id"]
        report.update(kernel_id=kernel_id, code_file=str(args.code), messages=[], execution_confirmed=False)
        save()  # Keep the kernel ID even if connection/execution fails later.
        print("Persistent kernel: " + kernel_id, flush=True)
        session_id, message_id = uuid.uuid4().hex, uuid.uuid4().hex
        cookie = "; ".join(key + "=" + value for key, value in session.cookies.items())
        socket = websocket.create_connection(
            base.replace("http://", "ws://") + "/api/kernels/" + kernel_id
            + "/channels?session_id=" + session_id, cookie=cookie, origin=base, timeout=15,
            http_no_proxy=[origin.hostname])
        socket.settimeout(5)
        request = {"header": {"msg_id": message_id, "username": "autohls", "session": session_id,
                   "date": datetime.now(timezone.utc).isoformat(), "msg_type": "execute_request",
                   "version": "5.3"}, "parent_header": {}, "metadata": {}, "channel": "shell",
                   "content": {"code": code, "silent": False, "store_history": True,
                               "user_expressions": {}, "allow_stdin": False, "stop_on_error": True}}
        reply, idle = False, False
        deadline = time.monotonic() + 45
        try:
            socket.send(json.dumps(request))
            while not (reply and idle):
                if time.monotonic() > deadline:
                    raise TimeoutError("Execution unconfirmed; kernel retained. Do not retry blindly.")
                try:
                    message = json.loads(socket.recv())
                except websocket.WebSocketTimeoutException:
                    continue
                if message.get("parent_header", {}).get("msg_id") != message_id:
                    continue
                kind, content = message["header"]["msg_type"], message["content"]
                report["messages"].append({"type": kind, "content": content})
                save()
                if kind == "stream":
                    print(content["text"], end="", flush=True)
                if kind == "error":
                    print(content["ename"] + ": " + content["evalue"], flush=True)
                if kind == "execute_reply":
                    report["status"] = content["status"]
                    reply = True
                idle = idle or (kind == "status" and content["execution_state"] == "idle")
            report["execution_confirmed"] = True
        finally:
            socket.close()  # The server-side kernel and its allocations stay alive.
            save()
        if report.get("status") != "ok":
            raise RuntimeError("Board execution failed; see saved messages; kernel retained")
    save()
    if args.action != "run":
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
