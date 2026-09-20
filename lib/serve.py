"""Serve a web export, because it cannot run from disk.

`index.html` pulls `index.wasm` and `index.pck` with `fetch()`, and a browser
refuses every fetch on a `file://` page -- the loader stops with "Failed to
fetch", which reads like a broken export and is not one. The `.wasm` also has to
arrive as `application/wasm` or it cannot be compiled while it downloads, and a
file off the disk carries no type at all.

The cross-origin headers are sent even though a `thread_support=false` export
runs without them: they cost nothing, and the day a preset turns threads on, a
build without `SharedArrayBuffer` stops at a black screen with a console error
nobody connects to this file. Except under `--plain`: the same headers make the
page refuse every cross-origin script without CORP -- a store SDK's CDN copy,
its ad providers -- so a store's QA tool pointed at this server reports an ad
blocker and never shows an ad. Thread-less exports only.
"""

from __future__ import annotations

import functools
import http.server
import socket
import socketserver
from pathlib import Path


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm",
        ".pck": "application/octet-stream",
        ".js": "text/javascript",
    }
    # Off for `--plain`: the isolation headers make the page refuse every
    # cross-origin script without CORP -- a store SDK's CDN copy, its ad
    # providers -- so a store's QA tool loading this server sees "AdBlock
    # detected" and never shows an ad. A thread-less export needs none of it.
    isolate = True

    def end_headers(self):
        if self.isolate:
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # quieter than the default one line per file
        if not str(args[1] if len(args) > 1 else "").startswith("2"):
            super().log_message(fmt, *args)


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def serve(directory: Path, port: int, isolate: bool = True) -> int:
    if not (directory / "index.html").is_file():
        raise SystemExit(f"no index.html in {directory} -- export the web target first")
    if not port_is_free(port):
        raise SystemExit(f"port {port} is already in use -- pass --port, or stop the holder")
    Handler.isolate = isolate
    handler = functools.partial(Handler, directory=str(directory))
    # Threaded: the loader fetches the wasm, the pck and the worklets at once,
    # and on a single-threaded server every one of them waits for the biggest.
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), handler) as server:
        print(f"serving  {directory}")
        print(f"open     http://127.0.0.1:{port}/")
        print("Ctrl+C stops it.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0
