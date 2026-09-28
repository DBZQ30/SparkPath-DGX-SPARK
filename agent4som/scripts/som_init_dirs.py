import os
import sys
from pathlib import Path

# Add hermes to sys.path to import constants
sys.path.append(str(Path(__file__).parent.parent / "hermes"))

try:
    from hermes_constants import get_kn_data_base
except ImportError:
    # Fallback if hermes is not correctly positioned
    def get_kn_data_base():
        val = os.environ.get("KN_DATA_BASE", "/data/agent4som/kn_data").strip()
        return Path(val)

def init_som_dirs():
    base = get_kn_data_base()
    print(f"Initializing SOM directories at: {base}")

    subdirs = ["global", "assistants", "users"]
    for sub in subdirs:
        path = base / sub
        print(f"  Creating {path}...")
        path.mkdir(parents=True, exist_ok=True)

    print("Initialization complete.")

if __name__ == "__main__":
    init_som_dirs()
