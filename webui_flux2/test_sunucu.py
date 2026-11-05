"""Test sunucusu: 8199 portunda WebUI'yi yeni kodla calistirir."""
import sys
import os
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server
from http.server import ThreadingHTTPServer

httpd = ThreadingHTTPServer(("127.0.0.1", 8199), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
print("test sunucu 8199", flush=True)
time.sleep(3600)