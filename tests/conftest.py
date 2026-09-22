import os
import sys

# Make the repo root importable so tests can reach orchestrator/, observability/
# and the adapters.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
