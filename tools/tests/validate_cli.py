"""Imports tools/validate.py as a module for the tests."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("validate_script", Path(__file__).resolve().parent.parent / "validate.py")
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)
main = module.main
