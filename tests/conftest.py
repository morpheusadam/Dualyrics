import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# keep tests away from the real %APPDATA%\Dualyrics
os.environ["DUALYRICS_HOME"] = tempfile.mkdtemp(prefix="dualyrics-test-")
