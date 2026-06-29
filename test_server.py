import http.server
import socketserver

PORT = 8000

class TestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Hello, World!')
        return

with socketserver.TCPServer(("0.0.0.0", PORT), TestHandler) as httpd:
    print(f"Server running at http://0.0.0.0:{PORT}")
    httpd.serve_forever()