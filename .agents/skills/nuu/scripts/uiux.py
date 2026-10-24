"""Compatibility shim re-exporting nuu."""

import nuu as _nuu
from nuu import *
from nuu import main

__all__ = [name for name in dir(_nuu) if not name.startswith("_")]

if __name__ == "__main__":
  raise SystemExit(main())
