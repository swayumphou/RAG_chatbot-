"""Local web chat.

    python src\\web_app.py              then open http://127.0.0.1:8765
    python src\\web_app.py --port 8766  if the port is already in use

Thin like the Telegram bot: one answer() call per question, rendered by the same render().
The page asks for English or Khmer on the first visit and sends that choice with every question.
The server listens on this computer only (127.0.0.1). Press Ctrl+C to stop it.
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
from answer import KB, LANGUAGES, answer, render

MAX_BODY, MAX_QUESTION = 10_000, 500


class Handler(BaseHTTPRequestHandler):
    kb: KB                                             # set once in main()

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if self.path.split("?")[0] in ("/", "/index.html"):
            self._send(200, config.WEB_PAGE.read_bytes(), "text/html; charset=utf-8")
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            length = min(int(self.headers.get("Content-Length", 0)), MAX_BODY)
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            return self._json(400, {"error": "The request was not valid JSON."})

        if self.path == "/api/ask":
            question = str(data.get("question", "")).strip()[:MAX_QUESTION]
            if not question:
                return self._json(400, {"error": "Please type a question."})
            language = data.get("language") if data.get("language") in LANGUAGES else None   # chosen on the page
            try:
                result = answer(question, self.kb, surface="web", language=language)
            except Exception as exc:                   # e.g. Ollama is not running
                return self._json(500, {"error": str(exc)})
            self._json(200, {"query_id": result["query_id"], "mode": result["mode"],
                             "html": render(result, html=True), "suggestions": result["suggestions"]})
        elif self.path == "/api/rate":
            with self.kb.lock:
                self.kb.db.rate(str(data.get("query_id", "")), 1 if data.get("rating") == 1 else -1)
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, *args) -> None:              # keep the terminal quiet
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local web chat.")
    parser.add_argument("--port", type=int, default=config.WEB_PORT)
    args = parser.parse_args()

    Handler.kb = KB()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        raise SystemExit(f"Port {args.port} is already in use. Try: python src\\web_app.py --port {args.port + 1}")
    print(f"Open http://127.0.0.1:{args.port} in your browser. Press Ctrl+C here to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
