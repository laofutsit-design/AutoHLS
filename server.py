"""Zero-dependency HTTP server for the AutoHLS competition demo."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from autohls import __version__
from autohls.core import DEVICE, explore
from autohls.vitis import detect_vitis


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
EXAMPLES = ROOT / "examples"
MAX_BODY_BYTES = 1_000_000
MODEL_SUITE_REPORT = ROOT / "artifacts/model-suite-20260927/audit.json"
BUDGET16_SUITE_REPORT = ROOT / "artifacts/model-budget16-20260927/audit.json"
ORDER_SUITE_REPORT = ROOT / "artifacts/model-order-suite-20260927/v1/final-audit.json"
COVERAGE_SUITE_REPORT = ROOT / "artifacts/model-coverage-suite-20260927/v1/final-audit.json"
PREFIXSUM_SUITE_REPORT = ROOT / "artifacts/model-prefixsum-suite-20260927/v1/final-audit.json"
DELIVERY_ROOT = ROOT / "artifacts/delivery-upgrade-20260927"
REPLAY_ROOT = ROOT / "artifacts/channel-replay-20260927/v1"


class AutoHLSHandler(BaseHTTPRequestHandler):
    server_version = "AutoHLS/0.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        if path == "/api/channel-replay":
            from scripts.channel_replay import view
            scenarios = parse_qs(urlparse(self.path).query, keep_blank_values=True).get('scenario', ['matched'])
            if len(scenarios) != 1 or scenarios[0] not in ('matched', 'bypass', 'drift'):
                self._error(HTTPStatus.BAD_REQUEST, '请选择 matched、bypass 或 drift 场景')
                return
            try:
                self._json(view(REPLAY_ROOT, scenarios[0]))
            except (OSError, ValueError, KeyError, TypeError):
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, '应用证据缺失或校验失败，不展示通过状态')
            return
        if path == "/api/delivery":
            from hardware.delivery_view import status
            try:
                self._json(status(DELIVERY_ROOT))
            except (OSError, ValueError, KeyError, TypeError):
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "交付证据缺失或校验失败，不显示通过状态")
            return
        if path == "/api/model-suite":
            suites = parse_qs(urlparse(self.path).query, keep_blank_values=True).get("suite", ["budget8"])
            reports = {"budget8": MODEL_SUITE_REPORT, "budget16": BUDGET16_SUITE_REPORT,
                       "order39": ORDER_SUITE_REPORT, "coverage42": COVERAGE_SUITE_REPORT,
                       "prefixsum14": PREFIXSUM_SUITE_REPORT}
            if len(suites) != 1 or suites[0] not in reports:
                self._error(HTTPStatus.BAD_REQUEST, "请选择 budget8、budget16、order39、coverage42 或 prefixsum14 实验批次")
                return
            report_path = reports[suites[0]]
            if not report_path.is_file():
                self._json({"available": False})
                return
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "实验审计文件暂不可读，请稍后重试")
                return
            self._json({"available": True, "report": report})
            return
        if path == "/api/research":
            runs = []
            for directory in sorted((ROOT / "artifacts" / "research").glob("*"), reverse=True)[:50]:
                manifest, summary = directory / "manifest.json", directory / "summary.json"
                if not manifest.is_file() or not summary.is_file():
                    continue
                try:
                    metadata = json.loads(manifest.read_text(encoding="utf-8"))
                    result = json.loads(summary.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue  # A still-running experiment can be between writes.
                runs.append({"id": directory.name, "benchmark": metadata["benchmark"],
                             "backend": metadata["backend"], "planner": metadata["planner"], "summary": result})
            self._json({"runs": runs})
            return
        if path == "/api/status":
            self._json(
                {
                    "ok": True,
                    "version": __version__,
                    "device": DEVICE,
                    "vitis": detect_vitis(),
                    "examples": [item.stem for item in sorted(EXAMPLES.glob("*.cpp"))],
                }
            )
            return
        if path.startswith("/api/examples/"):
            name = Path(unquote(path.removeprefix("/api/examples/"))).stem
            candidate = EXAMPLES / f"{name}.cpp"
            if not candidate.is_file() or candidate.parent != EXAMPLES:
                self._error(HTTPStatus.NOT_FOUND, "示例不存在")
                return
            self._json({"name": name, "source": candidate.read_text(encoding="utf-8")})
            return
        self._static(path)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY_BYTES:
                raise ValueError("请求体大小不合法")
            # Drain bounded bodies before closing HTTP/1.0, even on rejected routes.
            raw = self.rfile.read(length)
            if path != "/api/explore":
                self._error(HTTPStatus.NOT_FOUND, "接口不存在")
                return
            if length == 0:
                raise ValueError("请求体大小不合法")
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求必须是 JSON 对象")
            result = explore(
                source=str(payload.get("source", "")),
                top=str(payload.get("top", "")).strip() or None,
                goal=str(payload.get("goal", "balanced")),
            )
            self._json(result)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "无法解析 JSON 请求")
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # keep the demo API boundary explicit
            print(f"exploration failed: {exc}", file=sys.stderr)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "探索任务执行失败")

    def _static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else unquote(path).lstrip("/")
        requested = (WEB_ROOT / relative).resolve()
        try:
            requested.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self._error(HTTPStatus.FORBIDDEN, "禁止访问")
            return
        if not requested.is_file():
            self._error(HTTPStatus.NOT_FOUND, "页面不存在")
            return
        content_type, _ = mimetypes.guess_type(requested.name)
        data = requested.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"ok": False, "error": message}, status)

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AutoHLS local dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), AutoHLSHandler)
    print(f"天枢智构 AutoHLS 已启动: http://{args.host}:{args.port}")
    print("按 Ctrl+C 停止服务")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
