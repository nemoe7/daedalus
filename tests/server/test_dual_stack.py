import socket
import threading
import time

import httpx
import uvicorn

from daedalus.server import api


def find_open_port() -> int:
  """Find an unused TCP port."""
  with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind(("", 0))
    return s.getsockname()[1]


def test_dual_stack_listen() -> None:
  """Test that the server accepts connections on both IPv4 and IPv6."""
  port = find_open_port()
  host = None if api.HOST in ("0.0.0.0", "::") else api.HOST
  config = uvicorn.Config(
    api.app, host=host, port=port, log_config=None, access_log=False
  )
  server = uvicorn.Server(config)
  thread = threading.Thread(target=server.run)
  thread.start()
  time.sleep(1)

  try:
    r4 = httpx.get(f"http://127.0.0.1:{port}/health")
    assert r4.status_code == 200
    r6 = httpx.get(f"http://[::1]:{port}/health")
    assert r6.status_code == 200
    rl = httpx.get(f"http://localhost:{port}/health")
    assert rl.status_code == 200
  finally:
    server.should_exit = True
    thread.join()
