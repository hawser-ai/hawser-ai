"""Repository-wide pytest setup, loaded before any test module is imported."""

import os

# The CLI's Rich consoles are created at import time and read the terminal width once. Pin it so
# CLI output (and the tests' substring checks) doesn't wrap differently on an 80-column CI runner.
os.environ["COLUMNS"] = "200"
