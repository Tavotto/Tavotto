#!/usr/bin/env bash
# 把产品供应好的私有 Python 挂进一个**没有 Python 的空镜像**里真起、建 venv、离线装科学栈、出图
# （统一实施包 U05，ADR 0064 三档证据里「Linux 空镜像上的 runtime 可用性」这一条）。
#
# 主语说清楚：量的是「这份 runtime 在只有 glibc 的目标上能不能当 base」——不是产品入口、不是资格。
# 先证明镜像里确实没有 python3 / python（目标系统清单），再让 runtime 只读挂载（像装好的东西那样），
# venv 与产物落在容器里的可写目录。任一步非零就非零。
#
# 用法：
#   private_python_empty_image.sh <runtime_dir> <python_rel> <wheelhouse> <out_dir> [image]
#     runtime_dir  宿主上 `<data_dir>/private-python/runtimes/<id>`（TestRealChain 的 report.runtime_dir）
#     python_rel   锁文件里的 python_rel（Linux 是 bin/python3）
#     wheelhouse   宿主上装着**目标平台** matplotlib / numpy 及其依赖 wheel 的目录（pip download 的产物）
#     out_dir      宿主目录：容器把 report.json 与 Fig1.pdf 写到这里
#     image        默认 ubuntu:24.04（官方镜像不带 python3）
set -euo pipefail

RUNTIME_DIR=$1
PYTHON_REL=$2
WHEELHOUSE=$3
OUT_DIR=$4
IMAGE=${5:-ubuntu:24.04}

mkdir -p "${OUT_DIR}"
test -x "${RUNTIME_DIR}/${PYTHON_REL}"

docker run --rm \
  --network none \
  -v "${RUNTIME_DIR}:/rt:ro" \
  -v "${WHEELHOUSE}:/wh:ro" \
  -v "${OUT_DIR}:/out" \
  -e "PYTHON_REL=${PYTHON_REL}" \
  "${IMAGE}" bash -euo pipefail -c '
    # 1. 目标系统清单：镜像里没有系统 Python，也没有 pip / uv
    for name in python3 python pip pip3 uv; do
      if command -v "$name" >/dev/null 2>&1; then echo "镜像里有 $name：不是空目标" >&2; exit 9; fi
    done
    set -a; . /etc/os-release; set +a
    echo "image: $PRETTY_NAME  glibc: $(ldd --version | head -1)"
    PY="/rt/${PYTHON_REL}"
    # 2. 真起：自报身份，prefix 是挂载点
    "$PY" -I -c "import sys, json, platform; print(json.dumps({\"version\": platform.python_version(), \"prefix\": sys.prefix, \"executable\": sys.executable}))" | tee /out/launch.json
    # 3. venv 落在可写目录；runtime 是只读的（venv 不往 base 写）
    "$PY" -m venv /work/venv
    /work/venv/bin/python -m pip --version
    # 4. 离线装科学栈（--network none：连不上任何索引，只能来自 /wh）
    /work/venv/bin/python -m pip install --no-index --find-links /wh --only-binary=:all: --disable-pip-version-check matplotlib numpy
    /work/venv/bin/python -m pip check
    # 5. 出图
    mkdir -p /work/fig && cd /work/fig
    printf "%s\n" "import numpy as np" "import matplotlib" "matplotlib.use(\"Agg\")" "import matplotlib.pyplot as plt" \
      "x = np.array([1.0, 2.0, 3.0])" "fig, ax = plt.subplots()" "ax.plot(x, 3 * x + 1)" "ax.set_title(\"u05-empty-image\")" \
      "fig.savefig(\"Fig1.pdf\")" > figure.py
    MPLCONFIGDIR=/work/mpl /work/venv/bin/python figure.py
    test "$(stat -c %s Fig1.pdf)" -gt 1000
    cp Fig1.pdf /out/Fig1.pdf
    /work/venv/bin/python - <<"PY" > /out/report.json
import json, sys, os, matplotlib, numpy
print(json.dumps({
  "image": os.environ.get("PRETTY_NAME", ""),
  "python": sys.version.split()[0],
  "prefix": sys.prefix,
  "base_prefix": sys.base_prefix,
  "matplotlib": matplotlib.__version__,
  "numpy": numpy.__version__,
  "pdf_bytes": os.path.getsize("/work/fig/Fig1.pdf"),
}, indent=1))
PY
    cat /out/report.json
  '
echo "empty-image chain OK: ${OUT_DIR}/report.json"
