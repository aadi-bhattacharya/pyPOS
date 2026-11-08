"""No network, browser, shop DB, or native executable required."""
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from pos import managed_launcher as ml
from pos.instance_lock import instance_active, instance_lock
from pos.platform_paths import binary_names
from pos.server_lifecycle import serve_register


class InstanceLockTests(unittest.TestCase):
    def test_file_left_by_crash_is_not_a_live_instance(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "instance.lock").write_bytes(b"\0")
            self.assertFalse(instance_active(td))
            with instance_lock(td) as acquired:
                self.assertTrue(acquired)
                self.assertTrue(instance_active(td))
            self.assertFalse(instance_active(td))

    def test_lock_prevents_second_process_and_recovers_after_exit(self):
        with tempfile.TemporaryDirectory() as td:
            script = ("from pos.instance_lock import instance_lock; import sys, time; "
                      "with_block = instance_lock(sys.argv[1]); "
                      "entered = with_block.__enter__(); "
                      "print('READY' if entered else 'FAILED', flush=True); "
                      "time.sleep(1.5); with_block.__exit__(None, None, None)")
            with subprocess.Popen([sys.executable, "-c", script, td],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True) as child:
                ready = child.stdout.readline().strip()
                self.assertEqual(ready, "READY", child.stderr.read() if ready != "READY" else "")
                self.assertTrue(instance_active(td))
                with instance_lock(td) as acquired:
                    self.assertFalse(acquired)
                child.wait(timeout=5)
            self.assertFalse(instance_active(td))

    def test_no_second_app_spawn_when_lock_is_held(self):
        with tempfile.TemporaryDirectory() as td:
            with instance_lock(td) as acquired, patch.object(ml, "_run_locked") as run:
                self.assertTrue(acquired)
                self.assertEqual(ml.managed_run(Path(td), Path(td) / "versions" / "1.0.0"), 0)
                run.assert_not_called()

    def test_start_does_not_activate_update_while_another_till_is_running(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pending.txt").write_text("1.1.0", encoding="ascii")
            with instance_lock(root) as acquired, patch.object(ml, "webbrowser") as browser, \
                 patch.object(ml, "_start_child") as spawn:
                self.assertTrue(acquired)
                ml.stable_launch(root)
                browser.open.assert_called_once_with("http://127.0.0.1:8420/")
                spawn.assert_not_called()
            self.assertEqual((root / "pending.txt").read_text(), "1.1.0")

    def test_stable_forwarder_is_refreshed_from_verified_version(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            version_dir = root / "versions" / "1.2.3"
            version_dir.mkdir(parents=True)
            stable = root / ("pyPOS.exe" if os.name == "nt" else "pypos")
            stable.write_bytes(b"old forwarder")
            source = version_dir / binary_names()[1]
            source.write_bytes(b"verified replacement")
            with patch.object(ml, "verify_installed", return_value=True):
                self.assertTrue(ml._refresh_stable_forwarder(root, version_dir))
                self.assertFalse(ml._refresh_stable_forwarder(root, version_dir))
            self.assertEqual(stable.read_bytes(), b"verified replacement")

    def test_unverified_forwarder_is_never_installed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "versions" / "1.2.3"
            folder.mkdir(parents=True)
            stable = root / ("pyPOS.exe" if os.name == "nt" else "pypos")
            stable.write_bytes(b"old forwarder")
            (folder / binary_names()[1]).write_bytes(b"untrusted")
            with patch.object(ml, "verify_installed", return_value=False):
                with self.assertRaises(ml.UpdateError):
                    ml._refresh_stable_forwarder(root, folder)
            self.assertEqual(stable.read_bytes(), b"old forwarder")

    def test_extracted_release_cannot_replace_running_version(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with instance_lock(root) as acquired, patch.object(ml, "install_root", return_value=root), \
                 patch.object(ml, "_native_binary", return_value=root / "download" / "pypos-launcher"), \
                 patch.object(ml, "first_install") as install, \
                 patch.object(ml, "webbrowser") as browser:
                self.assertTrue(acquired)
                self.assertEqual(ml.main([]), 0)
                install.assert_not_called()
                browser.open.assert_called_once()

    def test_stop_waits_until_supervisor_releases_lock(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(ml, "instance_active", side_effect=[True, True, False]), \
                 patch.object(ml, "confirm_stop", return_value=True), \
                 patch.object(ml.time, "sleep", return_value=None), \
                 patch.object(ml, "show_error") as error:
                self.assertEqual(ml.request_stop(Path(td)), 0)
                self.assertEqual((Path(td) / "stop.request").read_text(), "stop\n")
                error.assert_not_called()


class ShutdownTests(unittest.TestCase):
    def test_incomplete_sale_does_not_have_its_connection_forcibly_closed(self):
        with tempfile.TemporaryDirectory() as td:
            calls = []
            finished = threading.Event()
            class Dispatcher:
                def shutdown(self, **kwargs):
                    calls.append("drain")
                    return calls.count("drain") >= 2
            class Asyncore:
                def close_all(self, channel_map):
                    calls.append("channels")
                    finished.set()
            class Server:
                task_dispatcher = Dispatcher()
                asyncore = Asyncore()
                def close(self):
                    calls.append("socket")
                def run(self):
                    if not finished.wait(4):
                        raise AssertionError("Stop never retried after slow checkout")
            request = Path(td) / "stop.request"
            request.write_text("stop\n")
            serve_register(Server(), {}, request)
            self.assertEqual(calls, ["socket", "drain", "socket", "drain", "channels"])

    def test_stop_finishes_existing_requests_and_closes_waitress_map(self):
        with tempfile.TemporaryDirectory() as td:
            events = []
            done = threading.Event()
            class Dispatcher:
                def shutdown(self, **kwargs):
                    events.append(("workers", kwargs))
                    return True
            class Asyncore:
                def close_all(self, channels):
                    events.append(("channels", channels))
                    done.set()
            class Server:
                task_dispatcher = Dispatcher()
                asyncore = Asyncore()
                def close(self):
                    events.append(("socket",))
                def run(self):
                    self_result = done.wait(4)
                    if not self_result:
                        raise AssertionError("Shutdown watcher never closed Waitress channels")
            request = Path(td) / "stop.request"
            threading.Timer(0.1, lambda: request.write_text("stop\n")).start()
            channels = {}
            serve_register(Server(), channels, request)
            self.assertEqual([e[0] for e in events], ["socket", "workers", "channels"])
            self.assertEqual(events[1][1]["cancel_pending"], False)
            self.assertTrue(request.exists(), "Only the supervising launcher clears the stop request")


if __name__ == "__main__":
    unittest.main()


class SidebarSwitchTests(unittest.TestCase):
    def test_logout_always_available_below_permission_filtered_nav(self):
        index = (Path(__file__).resolve().parents[1] / "pos" / "templates" / "index.html").read_text(encoding="utf-8")
        nav_end = index.index("</nav>")
        switch = index.index('id="switch-account"')
        self.assertLess(nav_end, switch)
        self.assertIn('href="/staff-logout"', index)
        self.assertIn('id="sidebar-operator"', index)
        self.assertLess(switch, index.index("</aside>"))
