"""Run the suite with the production vault, secrets and network inaccessible."""
from pathlib import Path
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["CALL_PIPELINE_NO_DOTENV"] = "1"
sys.dont_write_bytecode = True
FORBIDDEN = os.path.normcase(str(ROOT.parent / "Call"))


def guard(event, args):
    if event in {"open", "os.listdir", "os.scandir", "os.remove", "os.rename", "os.mkdir", "os.rmdir"}:
        for arg in args[:2]:
            if isinstance(arg, (str, bytes, os.PathLike)):
                path = os.path.normcase(os.path.abspath(os.fsdecode(arg)))
                if path == FORBIDDEN or path.startswith(FORBIDDEN + os.sep) or os.path.basename(path) == ".env":
                    raise RuntimeError("Test isolation: production data or secrets access denied")
    if event == "socket.connect":
        raise RuntimeError("Test isolation: network access denied")


if __name__ == "__main__":
    sys.addaudithook(guard)
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
