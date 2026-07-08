from __future__ import annotations

import json
import mimetypes
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parent.parent))

from backend.connectors import source_health
from backend.storage import store


ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
HOST = "127.0.0.1"
PORT = 18787


class TrendRadarHandler(BaseHTTPRequestHandler):
    server_version = "TrendRadar/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api_get(parsed.path, parse_qs(parsed.query))
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/trend-searches":
            payload = self._read_json()
            result = store.create(
                query=str(payload.get("query", "")),
                markets=_string_list(payload.get("markets")) or ["US", "UK", "CA", "AU"],
                window_days=int(payload.get("window_days") or 30),
            )
            self._send_json({"search": result["search"], "summary": result["summary"]}, status=201)
            return
        self._send_json({"error": "Not found"}, status=404)

    def log_message(self, format: str, *args) -> None:
        return

    def _handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        if path == "/api/sources/health":
            self._send_json({"sources": source_health()})
            return

        if path.startswith("/api/trend-searches/") and path.endswith("/summary"):
            search_id = path.split("/")[3]
            result = store.get(search_id)
            if not result:
                self._send_json({"error": "Trend search not found"}, status=404)
                return
            self._send_json(result["summary"])
            return

        if path.startswith("/api/rankings/"):
            kind = path.rsplit("/", 1)[-1]
            result = store.latest_or_seed() if not query.get("search_id") else store.get(query["search_id"][0])
            if not result:
                self._send_json({"error": "Trend search not found"}, status=404)
                return
            key = {"content": "content", "creators": "creators", "products": "products", "opportunities": "opportunities"}.get(kind)
            if not key:
                self._send_json({"error": "Ranking type not found"}, status=404)
                return
            limit = int((query.get("limit") or ["40"])[0])
            self._send_json({"items": result[key][:limit], "search": result["search"]})
            return

        if path.startswith("/api/entities/"):
            parts = path.split("/")
            if len(parts) < 5:
                self._send_json({"error": "Entity path must include type and id"}, status=400)
                return
            entity_type, entity_id = parts[3], parts[4]
            result = store.latest_or_seed() if not query.get("search_id") else store.get(query["search_id"][0])
            if not result:
                self._send_json({"error": "Trend search not found"}, status=404)
                return
            collection_key = {"content": "content", "creator": "creators", "product": "products", "opportunity": "opportunities"}.get(entity_type)
            if not collection_key:
                self._send_json({"error": "Entity type not found"}, status=404)
                return
            entity = next((item for item in result[collection_key] if item["id"] == entity_id), None)
            if not entity:
                self._send_json({"error": "Entity not found"}, status=404)
                return
            related = _related_entities(result, entity.get("related_ids", []))
            self._send_json({"entity": entity, "related": related})
            return

        if path == "/api/bootstrap":
            result = store.latest_or_seed()
            self._send_json(
                {
                    "summary": result["summary"],
                    "search": result["search"],
                    "content": result["content"][:12],
                    "creators": result["creators"][:12],
                    "products": result["products"][:12],
                    "opportunities": result["opportunities"][:12],
                    "source_health": result["source_health"],
                }
            )
            return

        self._send_json({"error": "Not found"}, status=404)

    def _serve_static(self, path: str) -> None:
        target = WEB_DIR / ("index.html" if path in ("", "/") else path.lstrip("/"))
        try:
            resolved = target.resolve()
            if not str(resolved).startswith(str(WEB_DIR.resolve())) or not resolved.exists() or not resolved.is_file():
                self._send_json({"error": "Not found"}, status=404)
                return
            body = resolved.read_bytes()
        except OSError:
            self._send_json({"error": "Unable to read file"}, status=500)
            return
        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).upper() for item in value if str(item).strip()]


def _related_entities(result: dict, related_ids: list[str]) -> list[dict]:
    if not related_ids:
        return []
    all_items = result["content"] + result["creators"] + result["products"] + result["opportunities"]
    return [item for item in all_items if item["id"] in related_ids]


def run(host: str = HOST, port: int = PORT, open_browser: bool = False) -> None:
    server = ThreadingHTTPServer((host, port), TrendRadarHandler)
    local_url = f"http://127.0.0.1:{port}/"
    print(f"Trend radar running at http://{host}:{port}")
    print(f"Open dashboard: {local_url}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(local_url)).start()
    server.serve_forever()


if __name__ == "__main__":
    selected_port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("TREND_RADAR_PORT", PORT))
    selected_host = os.environ.get("TREND_RADAR_HOST", HOST)
    should_open_browser = os.environ.get("TREND_RADAR_OPEN_BROWSER") == "1"
    run(host=selected_host, port=selected_port, open_browser=should_open_browser)
