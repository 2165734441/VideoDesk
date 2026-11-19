"""Run the project tests against the source tree, not the packaged payload.

The bundled runtime ships a ``python313._pth`` that puts ``payload/app`` on
sys.path, so ``python -m unittest`` silently tests the last packaged copy.
Always use this runner (or ``python app.py``) while developing.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import integrations  # noqa: E402

if Path(integrations.__file__).resolve().parent != ROOT / 'integrations':
    raise SystemExit(f'正在测试打包副本而不是源码：{integrations.__file__}')

if __name__ == '__main__':
    tests = ROOT / 'tests'
    pattern = sys.argv[1] if len(sys.argv) > 1 else 'test_*.py'
    suite = unittest.defaultTestLoader.discover(str(tests), pattern=pattern, top_level_dir=str(tests))
    result = unittest.TextTestRunner(verbosity=2 if len(sys.argv) > 1 else 1).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
