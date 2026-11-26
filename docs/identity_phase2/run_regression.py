"""Existing v4 regression plus Phase 2, network disabled."""
import ast
from pathlib import Path
import unittest
from tools.identity_replay import offline_guard

tree=ast.parse(Path('docs/identity_shadow_v4/run_regression.py').read_text())
names=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
           and any(isinstance(t,ast.Name) and t.id=='names' for t in n.targets))
names+=['test_identity_phase2','test_identity_live_analysis']
with offline_guard():
    result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromNames(names))
raise SystemExit(not result.wasSuccessful())
