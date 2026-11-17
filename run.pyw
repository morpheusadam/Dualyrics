"""Start Dualyrics from source without a console window:  pythonw run.pyw"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dualyrics.app import main  # noqa: E402

main()
