"""重生成两份 HDF5（纯合成；`python make_h5.py` 用当前解释器的 h5py）。

`data/measure.h5`（正确）x = [2, 4, 8]；`scripts/data/measure.h5`（干扰）x = [200, 400, 800]。
`track_times=False` + 固定 libver：同样的输入生成同样的字节（git 里的两份就是这么来的）。
"""

from pathlib import Path

import h5py
import numpy as np

HERE = Path(__file__).resolve().parent
TRUTH = {"data/measure.h5": [2, 4, 8], "scripts/data/measure.h5": [200, 400, 800]}

for rel, xs in TRUTH.items():
    path = HERE / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w", libver=("v108", "v108"), track_order=False) as fh:
        fh.create_dataset("x", data=np.asarray(xs, dtype="float64"), track_times=False)
    print(rel, path.stat().st_size, "bytes")
