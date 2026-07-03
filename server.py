#!/usr/bin/env python3
"""
CCTV-13 直播代理服务器
代理央视新闻 (cctvnews.cctv.com) 的 HLS 直播流，解决 CORS 问题。
流使用标准 H.264 编码，浏览器可直接播放，无需转码。
auth_key 通过 fetch_cctvnews.py 定期刷新。
"""

import json
import os
import re
import subprocess
import sys
import threading
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHANNELS_FILE = os.path.join(BASE_DIR, "cctv_channels.json")
PORT = 8765
REFRESH_INTERVAL = 60  # 1 分钟自动刷新一次 auth_key
VENV_PYTHON = os.path.join(BASE_DIR, "venv", "bin", "python3")  # 虚拟环境 Python

_refresh_lock = threading.Lock()


def _fetch_url(url: str, timeout: int = 15) -> tuple[int, dict, bytes]:
    """从远端下载资源，返回 (status, headers, body)"""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://m-live.cctvnews.cctv.com/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, dict(resp.headers), resp.read()


class Handler(SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def log_message(self, format, *args):
        pass

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self._safe_write(body)

    def _safe_write(self, data: bytes):
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _cors_headers(self):
        return {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in self._cors_headers().items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/channels":
            self._handle_channels()
        elif parsed.path == "/api/proxy":
            qs = parse_qs(parsed.query)
            url = qs.get("url", [None])[0]
            if url:
                self._handle_proxy(url)
            else:
                self.send_response(400)
                self.end_headers()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/refresh":
            self._handle_refresh()
        else:
            self.send_response(404)
            self.end_headers()

    # ── API: 频道列表 ──────────────────────────────────────
    def _handle_channels(self):
        if not os.path.exists(CHANNELS_FILE):
            self._json({"channels": []})
            return
        try:
            with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
                channels = json.load(f)
            for ch in channels:
                original = ch["hls_url"]
                ch["original_url"] = original
                ch["hls_url"] = f"/api/proxy?url={urllib.request.quote(original, safe='')}"
            self._json({"channels": channels})
        except Exception:
            self._json({"channels": []})

    # ── API: 代理 HLS 资源（m3u8 + TS） ───────────────────
    def _handle_proxy(self, url: str):
        try:
            status, headers, body = _fetch_url(url)
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self._safe_write(str(e).encode())
            return

        is_m3u8 = ".m3u8" in url.split("?")[0]

        if is_m3u8:
            text = body.decode("utf-8", errors="replace")
            base_url = url.rsplit("/", 1)[0]

            def rewrite_ts(match):
                seg = match.group(1)
                if seg.startswith("http"):
                    abs_url = seg
                else:
                    abs_url = f"{base_url}/{seg}"
                return f"/api/proxy?url={urllib.request.quote(abs_url, safe='')}"

            text = re.sub(r"^(\S+\.ts(?:\?\S*)?)$", rewrite_ts, text, flags=re.MULTILINE)
            body = text.encode("utf-8")
            content_type = "application/vnd.apple.mpegurl"
        else:
            content_type = headers.get("Content-Type", "video/mp2t")

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in self._cors_headers().items():
            self.send_header(k, v)
        self.end_headers()
        self._safe_write(body)

    # ── API: 刷新频道（重新获取 auth_key） ─────────────────
    def _handle_refresh(self):
        if not _refresh_lock.acquire(blocking=False):
            self._json({"error": "刷新正在进行中"}, 409)
            return
        try:
            channels, log = _do_refresh()
            self._json({"channels": channels, "log": log})
        except subprocess.TimeoutExpired:
            self._json({"error": "刷新超时"}, 504)
        except Exception as e:
            self._json({"error": str(e)}, 500)
        finally:
            _refresh_lock.release()


class ThreadedHTTPServer(HTTPServer):
    """多线程处理，避免 HLS 并发请求阻塞"""
    def process_request(self, request, client_address):
        t = threading.Thread(target=self._handle, args=(request, client_address))
        t.daemon = True
        t.start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            pass
        finally:
            self.shutdown_request(request)


def _do_refresh() -> tuple[list, str]:
    """执行一次 auth_key 刷新，返回 (channels, log)"""
    print("刷新央视新闻 auth_key...", file=sys.stderr)
    result = subprocess.run(
        [VENV_PYTHON, os.path.join(BASE_DIR, "refresh_channels.py")],
        capture_output=True, timeout=300, cwd=BASE_DIR,
    )
    stderr = result.stderr.decode("utf-8", errors="replace")
    print(stderr, file=sys.stderr, end="")

    if os.path.exists(CHANNELS_FILE):
        with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
            channels = json.load(f)
        for ch in channels:
            ch["hls_url"] = f"/api/proxy?url={urllib.request.quote(ch['hls_url'], safe='')}"
        return channels, stderr
    return [], stderr


def _auto_refresh_loop():
    """后台线程：定时刷新 auth_key"""
    import time as _time
    while True:
        _time.sleep(REFRESH_INTERVAL)
        if _refresh_lock.acquire(blocking=False):
            try:
                _do_refresh()
            except Exception as e:
                print(f"自动刷新失败: {e}", file=sys.stderr)
            finally:
                _refresh_lock.release()


def main():
    # 启动后台自动刷新线程
    t = threading.Thread(target=_auto_refresh_loop, daemon=True)
    t.start()

    server = ThreadedHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"CCTV-13 直播服务器已启动: http://127.0.0.1:{PORT}", file=sys.stderr)
    print(f"自动刷新间隔: {REFRESH_INTERVAL // 60} 分钟", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止", file=sys.stderr)
        server.server_close()


if __name__ == "__main__":
    main()
