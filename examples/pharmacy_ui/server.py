"""Synthetic SSE protocol demo: python -m examples.pharmacy_ui.server.

This server is separate from the real Agent API. It never queries real inventory.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from meditouch.pharmacy_ui import encode_sse, simulated_events

ALLOWED_ORIGINS = {'http://localhost:3000', 'http://127.0.0.1:3000'}


class Handler(BaseHTTPRequestHandler):
    def cors(self):
        origin = self.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        if self.path != '/events':
            self.send_error(404)
            return
        if self.headers.get('Origin') and self.headers['Origin'] not in ALLOWED_ORIGINS:
            self.send_error(403)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 4096:
                raise ValueError()
        except ValueError:
            self.send_error(400)
            return
        self.rfile.read(length)  # Demo input intentionally does not change synthetic facts.
        self.send_response(200)
        self.cors()
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache, no-transform')
        self.send_header('X-Accel-Buffering', 'no')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        try:
            for event in simulated_events():
                self.wfile.write(encode_sse(event))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == '__main__':
    print('Synthetic pharmacy UI SSE demo: http://127.0.0.1:8766/events', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8766), Handler).serve_forever()
