"""四种身份（统一实施包 U09，ADR 0070；RC-075 ~ RC-078）：semantic / render / artifact / run 各答一个问题，互不回写。

| 身份 | 回答 | 由什么算 | 落点 |
|---|---|---|---|
| `semantic` | 这是**哪张图的哪种导出**（意图） | `exportreq.render_plan_ref()` 的 `plan_identity`：规范化请求 + 每份源的语义身份（回执公开身份 + patch hash；**不含**字节 hash、不含实例 id） | `RenderPlan.plan_identity` |
| `render` | **谁、怎么**把它画出来的（fingerprint，RC-076） | semantic + 后端名与版本 + 栅格器与版本 + 字体政策版本（allowlist 的 sha256）+ 像素参数（px / ppi / 透明） | `render_identity()` |
| `artifact` | 文件**长什么样** | 封口文件字节的 sha256（RC-077：等于发布的字节；**绝不**再写进文件本身——那是自引用） | `manifest["artifact"]["sha256"]` |
| `run` | **这一次**作业 | 作业 id（不透明 uuid）；进 manifest 只为把回执 / 轨迹 / 产物串成一次事件，**不进**上面三个 | `manifest["identity"]["run"]` |

must_fail 两条（registry）：run UUID 进语义 hash（RC-075）→ `test_the_run_identity_never_enters_semantic_or_render`；
同名新字体仍命中旧缓存（RC-076）→ 字体政策版本进 `render`。

**hash 相同不证明字节相同**（RC-078）：`render` 相同说的是「同一意图、同一渲染栈、同一参数」；跨机器 / 跨平台的
字节一致性不在本轮承诺里（严格 byte 模式非硬目标），`reproducibility` 段把这句话写在投影里。

纯标准库。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import BACKEND_NAME, BACKEND_VERSION
from .fonts import allowlist_path

IDENTITY_VERSION = 1

#: 可复现性的口径（RC-078）：`semantic` = 同一意图（同图同参数）；`visual` = 同一渲染栈下语义 / 视觉可复现；
#: `byte` = 字节逐位相同——本轮**不承诺**，只在同机同栈下作为观察项。
REPRODUCIBILITY = {
    "semantic": "同一 semantic 身份 = 同一张图、同一份导出意图",
    "visual": "同一 render 身份 = 同一渲染栈（后端 / 栅格器 / 字体政策 / 像素参数）下语义与视觉可复现",
    "byte": "字节逐位相同不由任何身份保证（跨平台 / 跨版本不承诺；同机同栈下只作观察）",
}


def _sha(payload: dict) -> str:
    canon = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


def fonts_policy_version() -> str:
    """字体政策版本 = allowlist 文件的 sha256 前 16 位（加一张脸 / 换一个 hash 都会变）。
    `preview.PreviewCache` 的缓存键与 `render_identity` 共用这一份。"""
    return hashlib.sha256(Path(allowlist_path()).read_bytes()).hexdigest()[:16]


def render_identity(
    semantic: str | None,
    *,
    backend: str = BACKEND_NAME,
    backend_version: str = BACKEND_VERSION,
    renderer: str | None = None,
    renderer_version: str | None = None,
    fonts_version: str | None = None,
    px: list[int] | tuple[int, int] | None = None,
    ppi: float | None = None,
    transparent: bool | None = None,
) -> str:
    """render fingerprint：所有**已知**会影响输出的资源身份（RC-076）。矢量输出没有栅格器与像素参数
    （`renderer=None` / `px=None`）——那是事实，不是漏了；不知道的一维如实是 None，而不是省略键
    （省略与 None 在规范化 JSON 里是两个不同的值，不会撞）。"""
    payload = {
        "identity_version": IDENTITY_VERSION,
        "semantic": semantic,
        "backend": backend,
        "backend_version": backend_version,
        "renderer": renderer,
        "renderer_version": renderer_version,
        "fonts_version": fonts_version,
        "px": [int(px[0]), int(px[1])] if px else None,
        "ppi": float(ppi) if ppi is not None else None,
        "transparent": transparent,
    }
    return _sha(payload)


def identities(
    *, semantic: str | None, render: str | None, artifact_sha256: str | None, run: str | None
) -> dict:
    """manifest 的 `identity` 段：四个字段并列，谁也不含谁（artifact 不进 render，run 不进任何一个）。"""
    return {
        "identity_version": IDENTITY_VERSION,
        "semantic": semantic,
        "render": render,
        "artifact": artifact_sha256,
        "run": run,
    }


__all__ = [
    "IDENTITY_VERSION",
    "REPRODUCIBILITY",
    "fonts_policy_version",
    "identities",
    "render_identity",
]
