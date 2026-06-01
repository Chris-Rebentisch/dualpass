"""Test-time path setup.

Ensures the in-tree `src/dualpass/` package is importable even when the
editable install's .pth file is unprocessable (Python 3.14 on macOS treats
.pth files carrying the auto-applied `com.apple.provenance` xattr as hidden
and silently skips them, which breaks `pip install -e .` for every project
on that platform).

For wheel installs (`pip install dualpass`) this file is a no-op — the
package is already on sys.path via site-packages.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
