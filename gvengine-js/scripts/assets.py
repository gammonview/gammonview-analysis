# SPDX-License-Identifier: MIT
"""Copy the pinned bgsage's default networks and bearoff database into dist/.

Run by build.sh under `uv run --with bgsage==<version>`, so what is copied is
that wheel's assets. Writes `manifest.json`: everything `engine.js` needs to
create the engine (strategy, hidden sizes, files in the order bgsage passes
them), with a sha256 per file so a deployment can pin them.
"""
import hashlib
import importlib.metadata
import json
import shutil
import sys
from pathlib import Path

from bgsage.weights import bearoff_db_path, default_weights

out, version = Path(sys.argv[1]), sys.argv[2]
assert importlib.metadata.version("bgsage") == version, "uv resolved a different bgsage"
(out / "models").mkdir(parents=True, exist_ok=True)
weights = default_weights()
sha = {}


def copy(src: Path, name: str) -> str:
    shutil.copyfile(src, out / name)
    sha[name] = hashlib.sha256((out / name).read_bytes()).hexdigest()
    return name


# The same network can fill more than one slot; it is shipped once.
models = [copy(Path(p), f"models/{Path(p).name}") for p in weights.weight_paths_list]
manifest = {
    "bgsage": version,
    "strategy_type": weights.strategy_type,
    "models": models,
    "hidden_sizes": list(weights.hidden_sizes_list),
    "bearoff": copy(Path(bearoff_db_path()), "bearoff_1sided.db"),
    "sha256": sha,
}
(out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
