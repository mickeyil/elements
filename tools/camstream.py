#!/usr/bin/env python3
"""Minimal MJPEG camera streamer. Open http://<host>:8090 in a browser."""

import subprocess, sys, threading
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
DEV = sys.argv[2] if len(sys.argv) > 2 else "/dev/video0"

frame = b""
lock = threading.Lock()

def capture_loop():
    global frame
    cmd = [
        "ffmpeg", "-f", "v4l2", "-video_size", "320x240", "-framerate", "10",
        "-i", DEV, "-c:v", "mjpeg", "-q:v", "8", "-f", "image2pipe",
        "-vcodec", "mjpeg", "-"
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    buf = b""
    while True:
        chunk = proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        # JPEG starts with FFD8, ends with FFD9
        while True:
            start = buf.find(b"\xff\xd8")
            end = buf.find(b"\xff\xd9", start + 2) if start >= 0 else -1
            if start >= 0 and end >= 0:
                with lock:
                    frame = buf[start:end + 2]
                buf = buf[end + 2:]
            else:
                break

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""<!DOCTYPE html>
<html><body style="margin:0;background:#000;display:flex;justify-content:center;align-items:center;height:100vh">
<img src="/stream" style="max-width:100%;max-height:100vh">
</body></html>""")
        elif self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while True:
                    with lock:
                        f = frame
                    if f:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + f + b"\r\n")
                    import time; time.sleep(0.1)
            except BrokenPipeError:
                pass
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass  # silence logs

threading.Thread(target=capture_loop, daemon=True).start()
print(f"Camera streaming on http://0.0.0.0:{PORT}")
HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
