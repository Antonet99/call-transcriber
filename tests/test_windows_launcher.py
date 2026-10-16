import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


def interrupt_synthetic_console(root: Path) -> None:
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    with (root / "output.txt").open("wb") as output:
        child = subprocess.Popen(
            [os.environ["COMSPEC"], "/d", "/c", str(root / "scripts" / "start_watcher.cmd")],
            stdout=output, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup,
        )
        try:
            deadline = time.monotonic() + 10
            while not (root / "ready").exists():
                if child.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Synthetic console did not become ready")
                time.sleep(0.02)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.FreeConsole()
            if not kernel.AttachConsole(child.pid):
                raise ctypes.WinError(ctypes.get_last_error())
            kernel.SetConsoleCtrlHandler(None, True)
            if not kernel.GenerateConsoleCtrlEvent(0, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            print(json.dumps({"exit_code": child.wait(timeout=5), "stopped": (root / "stopped").exists()}))
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=3)


@unittest.skipUnless(os.name == "nt", "Windows batch launcher")
class WindowsLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="launcher test! ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        (scripts / "__init__.py").write_text("", encoding="utf-8")
        self.module = scripts / "watch_calls.py"
        launcher = (Path(__file__).resolve().parent.parent / "scripts" / "start_watcher.cmd").read_text()
        launcher = launcher.replace('set "PYTHON_EXE=%ROOT_DIR%\\.venv\\Scripts\\python.exe"',
                                    f'set "PYTHON_EXE={sys.executable}"')
        self.launcher = scripts / "start_watcher.cmd"
        self.launcher.write_text(launcher, encoding="utf-8")

    def test_ctrl_c_stops_cleanly_without_batch_confirmation(self):
        self.module.write_text(
            'from pathlib import Path\nimport time\nPath("ready").write_text("ready")\n'
            'try:\n    while True: time.sleep(.05)\n'
            'except KeyboardInterrupt:\n    Path("stopped").write_text("clean")\n    raise SystemExit(17)\n',
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--interrupt-console", str(self.root)],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"exit_code": 17, "stopped": True})
        output = (self.root / "output.txt").read_text(encoding="utf-8", errors="replace")
        self.assertNotIn("S/N", output)
        self.assertNotIn("Y/N", output)

    def test_launcher_preserves_exit_codes_and_literal_arguments(self):
        for code in (0, 23):
            with self.subTest(code=code):
                self.module.write_text(
                    f'import json, sys\nprint(json.dumps(sys.argv[1:]))\nraise SystemExit({code})\n', encoding="utf-8",
                )
                result = subprocess.run(
                    f'"{os.environ["COMSPEC"]}" /d /s /c ""{self.launcher}" "literal!value" "two words""',
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(json.loads(result.stdout), ["literal!value", "two words"])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--interrupt-console":
        interrupt_synthetic_console(Path(sys.argv[2]))
    else:
        unittest.main()
