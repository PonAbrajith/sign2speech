r"""Launch Sign2Speech V2 Studio without installing the package.

    .venv\Scripts\python.exe run_studio.py [--workspace DIR] [--port COM9]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from sign2speech_v2.ui.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
