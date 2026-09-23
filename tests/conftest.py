"""Keep the suite independent of runtime state on this machine."""
import atexit
import os
import shutil
import tempfile
from pathlib import Path


# A local run of acquire_evds_series writes .lakehouse-runtime/on_demand.duckdb, and every
# lakehouse connection attaches it. Point the suite at an empty location so results never
# depend on what was acquired here; overlay tests set their own location explicitly.
_overlay_root = tempfile.mkdtemp(prefix="evds-overlay-")
atexit.register(shutil.rmtree, _overlay_root, True)
os.environ["EVDS_ON_DEMAND_DB"] = str(Path(_overlay_root) / "on_demand.duckdb")
