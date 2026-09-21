import http.server
import socketserver
import os
import webbrowser
import sys

PORT = 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

def main():
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        url = f"http://localhost:{PORT}"
        print(f"==================================================")
        print(f"⚡ AgentMesh Unified Control Portal Running")
        print(f"🌐 Access URL: {url}")
        print(f"⏳ Temporal UI: http://localhost:8233")
        print(f"🛡️ Agent Gateway: http://localhost:8080")
        print(f"==================================================")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down Control Portal...")

if __name__ == "__main__":
    main()
