"""
Global conftest: add the Hermes agent source tree to ``sys.path`` so that all
tests in this suite can ``from hermes_cli.tools_config import ...`` or
``from gateway.run import ...`` without manual path manipulation.
"""

from pathlib import Path
import sys

_HERMES_SRC = Path.home() / ".hermes" / "hermes-agent"
if _HERMES_SRC.is_dir() and str(_HERMES_SRC) not in sys.path:
    sys.path.insert(0, str(_HERMES_SRC))
