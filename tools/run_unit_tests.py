#!/usr/bin/env python3
"""Discover hardware-free unittest modules, excluding interactive camera scripts."""
import sys
import unittest
import importlib.util
import sysconfig
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if importlib.util.find_spec('gi') is None:
    system_packages = Path('/usr/lib/python3/dist-packages')
    extension = sysconfig.get_config_var('EXT_SUFFIX')
    if extension and (system_packages/'gi'/('_gi'+extension)).is_file():
        sys.path.append(str(system_packages))

if __name__ == '__main__':
    suite = unittest.TestSuite()
    for path in sorted((ROOT/'test').glob('test_*.py')):
        if 'unittest.TestCase' in path.read_text():
            suite.addTests(unittest.defaultTestLoader.discover(str(ROOT/'test'), pattern=path.name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
