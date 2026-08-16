"""静态扫描图库目录，起草 stem↔script 注册表（mm_registry.json 的数据来源）。

三件事：
  1. AST 识别入口方言：有 def main → "main"；否则 def render → "render"；
     否则只有 `if __name__ == "__main__"` 的内联脚本 → "__main__"
  2. 从 save()/savefig() 调用里抽 stem——字符串字面量直接取；
     f-string 把 {…} 变成 * 得到 glob 模式，再与磁盘上的 PDF/PNG 产物
     比对还原为具体 stem（动态命名无法纯静态求解，必须对着产物核）
  3. 同一 stem 被多个脚本认领 → 冲突显式报告，**绝不自动裁决**
     （裁决手写进 mm_registry.json；--write 合并时现有条目永远优先）

无法静态识别的（stem 全是变量、Path 拼接等）列入 unresolved 报告，
需要手工补登。cost 无法静态判断，草稿一律 "medium"，请按需修正。

用法：
    python -m engine.discover <figures_dir>            # 只打印报告
    python -m engine.discover <figures_dir> --write    # 生成/合并 mm_registry.json

纯标准库，Flask 父进程可安全 import。
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

from . import registry

OUT_EXTS = (".pdf", ".png", ".svg", ".jpg", ".jpeg")
SKIP_PREFIXES = ("paper_style", "_")  # 样式模块及其副本（"paper_style 2.py"）、私有助手
SAVE_FUNCS = {"save", "savefig"}


def _entry_of(tree: ast.Module) -> str | None:
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    if "main" in names:
        return "main"
    if "render" in names:
        return "render"
    for node in tree.body:
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"):
            return "__main__"
    return None


def _str_pattern(node: ast.expr) -> str | None:
    """字符串参数 → stem 模式；f-string 的插值段变 *。整体是变量则放弃。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        s = node.value
    elif isinstance(node, ast.JoinedStr):
        s = "".join(str(v.value) if isinstance(v, ast.Constant) else "*"
                    for v in node.values)
    else:
        return None
    s = s.strip().replace("\\", "/").rsplit("/", 1)[-1]  # 去目录前缀
    for ext in OUT_EXTS:
        if s.lower().endswith(ext):
            s = s[: -len(ext)]
            break
    if not s or s.startswith("*"):  # 空或开头即变量：无法定位
        return None
    return s


def _stem_patterns(tree: ast.Module) -> tuple[set[str], int]:
    """返回 (stem 模式集合, save/savefig 调用次数)。

    调用次数单独报出来，是为了区分「这个模块根本不产图」和「它明明在存图、
    但文件名是变量所以静态看不出来」——后者必须让用户看见，否则只会拿到一份
    空注册表而不知道为什么。
    """
    pats: set[str] = set()
    calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in SAVE_FUNCS:
            continue
        calls += 1
        for arg in node.args:
            pat = _str_pattern(arg)
            if pat:
                pats.add(pat)
    return pats, calls


def _resolve(patterns: set[str], figures_dir: Path) -> tuple[set[str], list[str]]:
    """模式 → 具体 stem。带 * 的与磁盘产物（pdf/png）比对；无匹配进 unresolved。"""
    stems: set[str] = set()
    unresolved: list[str] = []
    for pat in sorted(patterns):
        if "*" not in pat:
            stems.add(pat)
            continue
        found = {p.stem for ext in (".pdf", ".png")
                 for p in figures_dir.rglob(pat + ext)}
        if found:
            stems |= found
        else:
            unresolved.append(pat)
    return stems, unresolved


def discover(figures_dir: str | Path) -> dict:
    """扫描顶层 *.py（脚本约定放图库根目录），返回原始报告。"""
    figures_dir = Path(figures_dir)
    scripts: dict[str, dict] = {}
    for p in sorted(figures_dir.glob("*.py")):
        if p.name.startswith(SKIP_PREFIXES):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        entry = _entry_of(tree)
        pats, save_calls = _stem_patterns(tree)
        if entry is None or not save_calls:
            continue  # 没有入口，或压根不产图（纯数据模块）
        stems, unresolved = _resolve(pats, figures_dir)
        scripts[p.name] = {"entry": entry, "stems": sorted(stems),
                           "unresolved": unresolved,
                           # 在存图却一个 stem 都定位不到：文件名全在变量里
                           # （save_panel(fig, stem) → fig.savefig(pdf)）。
                           # 这种脚本进不了草稿，只能手工登记，必须报出来。
                           "dynamic_names": not stems,
                           "save_calls": save_calls}
    claims: dict[str, list[str]] = {}
    for script, info in scripts.items():
        for s in info["stems"]:
            claims.setdefault(s, []).append(script)
    conflicts = {s: sorted(cs) for s, cs in claims.items() if len(cs) > 1}
    return {"scripts": scripts, "conflicts": conflicts}


def build_draft(figures_dir: str | Path) -> tuple[dict, dict]:
    """报告 → 注册表草稿。冲突 stem 不分配给任何脚本，留给人工裁决。"""
    rep = discover(figures_dir)
    cfg: dict[str, dict] = {}
    for script, info in sorted(rep["scripts"].items()):
        stems = [s for s in info["stems"] if s not in rep["conflicts"]]
        if stems:
            cfg[script] = {"entry": info["entry"], "cost": "medium",
                           "notes": "", "stems": stems}
    return {"version": 1, "scripts": cfg}, rep


def write_config(figures_dir: str | Path, cfg: dict) -> Path:
    path = registry.registry_path(figures_dir)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def merge(figures_dir: str | Path) -> tuple[dict, dict, dict]:
    """草稿并入现有注册表：现有条目原样保留，只追加新脚本与未登记的 stem。

    返回 (合并后的配置, 原始报告, 变更摘要)。
    """
    draft, rep = build_draft(figures_dir)
    path = registry.registry_path(figures_dir)
    if not path.exists():
        changes = {"added_scripts": sorted(draft["scripts"]), "added_stems": {}}
        return draft, rep, changes

    merged = json.loads(path.read_text(encoding="utf-8"))
    scripts = merged.setdefault("scripts", {})
    registered = {s for c in scripts.values() for s in c.get("stems", [])}
    added_scripts: list[str] = []
    added_stems: dict[str, list[str]] = {}
    for script, cfg_s in draft["scripts"].items():
        fresh = [s for s in cfg_s["stems"] if s not in registered]
        if not fresh:
            continue
        registered.update(fresh)
        if script in scripts:
            scripts[script]["stems"] = list(scripts[script].get("stems", [])) + fresh
            added_stems[script] = fresh
        else:
            scripts[script] = {**cfg_s, "stems": fresh}
            added_scripts.append(script)
    return merged, rep, {"added_scripts": added_scripts, "added_stems": added_stems}


def _print_report(rep: dict) -> None:
    for script, info in sorted(rep["scripts"].items()):
        print(f"  {script}  [{info['entry']}]  {len(info['stems'])} stems")
        for pat in info["unresolved"]:
            print(f"    ? 无法与磁盘产物对上: {pat}*")
        if info.get("dynamic_names"):
            print(f"    ! {info['save_calls']} 处 save/savefig 的文件名是变量，"
                  "静态定位不到 stem —— 请把产出的 stem 手工写进 mm_registry.json"
                  "（引擎运行时按真实文件名捕获，登记后即可参数化）")
    if rep["conflicts"]:
        print("  ⚠ 归属冲突（未分配，请在 mm_registry.json 手工裁决）:")
        for stem, cs in sorted(rep["conflicts"].items()):
            print(f"    {stem}: {' vs '.join(cs)}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("figures_dir")
    ap.add_argument("--write", action="store_true",
                    help="生成/合并 mm_registry.json（现有条目优先）")
    args = ap.parse_args(argv)
    if not Path(args.figures_dir).is_dir():
        raise SystemExit(f"目录不存在: {args.figures_dir}")

    if args.write:
        cfg, rep, changes = merge(args.figures_dir)
        path = write_config(args.figures_dir, cfg)
        print(f"已写入 {path}")
        _print_report(rep)
        if changes["added_scripts"]:
            print(f"  + 新脚本: {', '.join(changes['added_scripts'])}")
        for script, stems in changes["added_stems"].items():
            print(f"  + {script} 新增 stems: {', '.join(stems)}")
        if not changes["added_scripts"] and not changes["added_stems"]:
            print("  （无新增，现有注册表未改动）")
    else:
        draft, rep = build_draft(args.figures_dir)
        _print_report(rep)
        n = sum(len(c["stems"]) for c in draft["scripts"].values())
        print(f"  草稿共 {len(draft['scripts'])} 个脚本 / {n} 个 stem"
              f"（--write 落盘；cost 默认 medium 请按需修正）")


if __name__ == "__main__":
    main()
