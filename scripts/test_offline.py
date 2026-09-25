"""Run backend tests with socket connections disabled in the test process.

Use the doudizhu-arena conda Python. HTTPX in-process transports remain usable.
The CLI regression subprocesses explicitly execute --mock.
"""
import os
from pathlib import Path
import socket
import sys

import pytest


def main():
    attempts = []
    def deny(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError('Network disabled by offline test runner')
    originals = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
    os.chdir(Path(__file__).resolve().parents[1] / 'src/backend')
    try:
        result = pytest.main(['-q', '-p', 'no:cacheprovider', *sys.argv[1:]])
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = originals
    print(f'Offline guard: {len(attempts)} attempted network connections')
    return 1 if attempts else int(result)


if __name__ == '__main__':
    raise SystemExit(main())
