"""Static demonstration frontend. Uses Python's standard library only."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit


def main(argv=None):
    parser = argparse.ArgumentParser(description="Tool1 standalone demonstration frontend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8521)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8520")
    parser.add_argument("--prefix", default="", help="Frontend URL mount, e.g. /tools/tool1")
    parser.add_argument("--directory", type=Path, default=None)
    args = parser.parse_args(argv)
    root = args.directory or Path(__file__).resolve().parents[1] / "acdcpf_opf" / "dashboard" / "static"
    if not root.is_dir():
        root = Path(__file__).resolve().parents[1] / "dashboard" / "static"
    if not root.is_dir():
        root = Path(__file__).resolve().parent  # standalone frontend archive
    prefix = "/" + args.prefix.strip("/") if args.prefix.strip("/") else ""

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if prefix and path == prefix:
                self.send_response(302)
                self.send_header("Location", prefix + "/")
                self.end_headers()
                return
            if prefix and not path.startswith(prefix + "/"):
                self.send_error(404)
                return
            self.path = self.path[len(prefix):]
            if urlsplit(self.path).path == "/config.js":
                data = ("window.TOOL1_CONFIG = " + json.dumps({"apiBaseUrl": args.backend_url}) + ";").encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            super().do_GET()

    print(f"Tool1 frontend: http://{args.host}:{args.port}{prefix}/", flush=True)
    server = ThreadingHTTPServer((args.host, args.port), partial(Handler, directory=str(root)))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
