"""Serve the built editor to desktop browser tests."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socket import SOMAXCONN


class EditorServer(ThreadingHTTPServer):
    # Chrome opens parallel connections for the scripts on a cold page load.
    request_queue_size = SOMAXCONN

    def __init__(self):
        handler = partial(SimpleHTTPRequestHandler, directory=Path(__file__).with_name("dist"))
        super().__init__(("127.0.0.1", 0), handler)
