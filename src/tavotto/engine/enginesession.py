"""「这个面板该由谁渲染」——**唯一**的判据（ADR 0021 §5 / §9.1）。

在这个模块之前，每条端点的形状都是

    worker = pool.get(script, project, entry)

而 native 会话**不在池里**（ADR 0021 §5）。于是每个端点都要长出一个

    if 有 native 会话: …
    else: pool.get(…)

这个形状有两个已知的失败模式，仓库里都出现过同族的：

1. **漏掉第二个消费点**——加分支的人只改了 `render`，`export` / `preview_png`
   / `preflight` 还是老样子。表现是"预览是 native 的、导出是 safe 的"，
   两张图不一样而界面什么都没说；
2. **静默 fallback**——`route_for()` 回 None 时顺手 `pool.get()` 兜一下。
   那一行看起来很稳健，实际是**用另一个环境生成的图冒充这一个**：
   用户的 conda 环境里 matplotlib 是 3.7、Tavotto 内置的是 3.10，同一个脚本
   出来的图可以肉眼可见地不同。

所以判据收在这里，**由 `execution_profile` 决定，不由"哪个碰巧可用"决定**。

## 两条硬规则

| panel 的 profile | live native route | 结果 |
|---|---|---|
| `native` | 在 | native 会话 |
| `native` | 不在 | **`native_session_offline`**，绝不退回 safe |
| `safe` | 在 | **仍然是 safe worker**，绝不因为"有个 native 会话"就切过去 |
| `safe` | 不在 | safe worker |

纯标准库 + `pool` / `nativesession`。
"""

from __future__ import annotations

from . import figcapture, nativesession, pool, runcodes, runtimeasset
from .runcodes import RunError

PROFILE_SAFE = figcapture.PROFILE_SAFE
PROFILE_NATIVE = figcapture.PROFILE_NATIVE


def profile_of(project_root, asset_id: str, *, default: str = PROFILE_SAFE) -> str:
    """这个 runtime 素材是哪一档跑出来的——**按物化 descriptor 的记载**。

    `execution_profile` 是描述符的字段（ADR 0013 / Session 2），也就是说它
    记的是"上一次这张图是怎么产生的"。cache 读不到时退回 `default`（safe）：
    **未知不等于 native**——把未知当 native 会让一个普通面板在没有会话时
    直接报 offline，而它本来 safe 就能渲染。
    """
    meta = runtimeasset.load_metadata(project_root, asset_id)
    if not isinstance(meta, dict):
        return default
    desc = meta.get("descriptor")
    if not isinstance(desc, dict):
        return default
    got = desc.get("execution_profile")
    return got if got in (PROFILE_SAFE, PROFILE_NATIVE) else default


def resolve(
    *,
    project_root: str,
    script: str,
    entry: str,
    stem: str,
    execution_profile: str = PROFILE_SAFE,
    registry=None,
):
    """按 profile 给出一个 **Worker-like** 的东西。

    回来的两种对象共享同一批方法名（`ensure_built` / `override` / `export` /
    `render_png` / `preview_png` / `svg_path` / `shutdown`），所以调用方**不必
    知道自己拿到的是哪一种**——这正是不写 `if native` 的前提。
    """
    del registry  # 预留：将来按注册表校验归属；现在解析已经在调用方做完了
    if execution_profile == PROFILE_NATIVE:
        session = nativesession.REGISTRY.route_for(project_root, script, stem)
        if session is None:
            # **不 fallback。** 这张图是用户自己那个 Python 画的，safe worker
            # 拿他的脚本重跑一遍得到的不是同一张图（不同的 matplotlib、
            # 不同的包、不同的 cwd），而界面上不会有任何提示。
            raise RunError(runcodes.NATIVE_SESSION_OFFLINE)
        return session
    # safe 侧：**即使**这个 (script, stem) 上正好挂着一条 native route 也不切
    # 过去。profile 是面板的属性，不是"现在哪条路通"。
    return pool.get(script, str(project_root), entry)


def is_native(worker_like) -> bool:
    """给少数**真的**需要分支的地方（写回硬拒绝、UI 标记）。

    判据是类型而不是鸭子方法：`hasattr(w, "resume")` 那种写法会在有人给
    `EngineWorker` 加一个同名方法的那天静默改变行为。
    """
    return isinstance(worker_like, nativesession.NativeSession)
