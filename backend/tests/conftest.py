import os
import tempfile

# Isolated data dir so tests never touch real contracts. Must run before the app is imported.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="obligo-test-")