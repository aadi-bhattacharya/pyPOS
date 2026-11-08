"""Shutdown for Waitress single-address servers, including worker threads."""
import logging
import threading
from pathlib import Path

LOG = logging.getLogger("pypos")


def serve_register(server, channel_map, stop_file=None):
    finished = threading.Event()
    stopping = threading.Event()
    stop_file = Path(stop_file) if stop_file else None

    def shutdown():
        if stopping.is_set():
            return False
        stopping.set()
        completed = False
        try:
            # BaseWSGIServer.close() alone leaves channels/worker threads.
            # Drain in-flight sales before closing their HTTP connections.
            server.close()
            clean = server.task_dispatcher.shutdown(cancel_pending=False, timeout=30)
            if clean is False:
                # Never sever an active sale's response just to make Stop fast.
                # The watcher retries; the caller reports a delayed shutdown.
                LOG.warning("Waiting for in-flight Waitress tasks before shutdown")
                return False
            server.asyncore.close_all(channel_map)
            completed = True
            return True
        finally:
            # A slow/hung request must not permanently disable future retries.
            if not completed:
                stopping.clear()

    def listen():
        while not finished.wait(0.2):
            if stop_file and stop_file.is_file():
                LOG.info("Graceful pyPOS shutdown requested")
                try:
                    if shutdown():
                        return
                except Exception:
                    LOG.exception("Waitress shutdown failed")

    if stop_file:
        threading.Thread(target=listen, name="pypos-stop", daemon=True).start()
    try:
        server.run()
    finally:
        finished.set()
        if not stopping.is_set():
            shutdown()
