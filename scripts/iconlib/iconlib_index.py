"""中英关键词索引（ADR 0222 §五）。先做关键词 + 同义词表，不做向量检索。

中文词表是**最小起步版**（常见细胞 / 器官 / 器材词，人工写）；没命中的词不翻译、不编，`keywords_zh` 只含命中的。
生物学术语机翻错误风险高，扩词表必须人工抽检。
"""

from __future__ import annotations

import re

GLOSSARY_ZH = {
    "cell": "细胞",
    "cells": "细胞",
    "nucleus": "细胞核",
    "mitochondrion": "线粒体",
    "mitochondria": "线粒体",
    "golgi": "高尔基体",
    "apparatus": "器",
    "endoplasmic": "内质网",
    "reticulum": "网",
    "ribosome": "核糖体",
    "lysosome": "溶酶体",
    "membrane": "膜",
    "receptor": "受体",
    "channel": "通道",
    "transporter": "转运体",
    "virus": "病毒",
    "bacteria": "细菌",
    "bacterium": "细菌",
    "dna": "DNA",
    "rna": "RNA",
    "protein": "蛋白",
    "neuron": "神经元",
    "macrophage": "巨噬细胞",
    "lymphocyte": "淋巴细胞",
    "t": "T",
    "antibody": "抗体",
    "blood": "血液",
    "heart": "心脏",
    "liver": "肝脏",
    "kidney": "肾",
    "kidneys": "肾",
    "lung": "肺",
    "lungs": "肺",
    "brain": "脑",
    "stomach": "胃",
    "intestine": "肠",
    "mouse": "小鼠",
    "rat": "大鼠",
    "fish": "鱼",
    "egg": "卵",
    "eggs": "卵",
    "tube": "管",
    "syringe": "注射器",
    "pipette": "移液器",
    "dish": "培养皿",
    "petri": "培养皿",
    "microscope": "显微镜",
    "plant": "植物",
    "sun": "太阳",
    "ribbon": "带",
    "single": "单",
    "stranded": "链",
    "tissue": "组织",
    "capillary": "毛细血管",
    "organelle": "细胞器",
    "vesicle": "囊泡",
    "lipid": "脂质",
    "enzyme": "酶",
}
CATEGORY_ZH = {
    "Human_physiology": "人体生理",
    "Animals": "动物",
    "Blood_Immunology": "血液与免疫",
    "General_items": "通用物品",
    "Lab_apparatus": "实验器材",
    "Microbiology": "微生物",
    "Intracellular_components": "胞内结构",
    "Nucleic_acids": "核酸",
    "Plants_Algae": "植物与藻类",
    "Chemistry": "化学",
    "Cell_membrane": "细胞膜",
    "Genetics": "遗传",
    "Parasites": "寄生虫",
    "Amino-Acids": "氨基酸",
    "Receptors_channels": "受体与通道",
    "Tissues": "组织",
    "Cell_culture": "细胞培养",
    "cell_element": "细胞元件",
    "cell_type": "细胞类型",
    "compound": "化合物",
    "human_tissue": "人体组织",
    "receptor": "受体",
    "transporter": "转运体",
}


def tokens(*texts: str) -> list[str]:
    """文本 → 小写词（按字母数字切，去重保序，去 1 个字符的杂碎，保留数字词）。"""
    seen: dict[str, None] = {}
    for t in texts:
        for w in re.split(r"[^A-Za-z0-9]+", t or ""):
            w = w.lower()
            if len(w) > 1 or w in GLOSSARY_ZH:
                seen.setdefault(w, None)
    return list(seen)


def build_entry(rec: dict, pre: dict) -> dict:
    """一条索引记录（Agent 搜图用，不含出处细节；出处在 manifest）。"""
    kw = tokens(
        rec.get("title", ""),
        rec.get("category", ""),
        rec.get("description", ""),
        " ".join(rec.get("synonyms") or []),
    )
    zh = list(dict.fromkeys(GLOSSARY_ZH[w] for w in kw if w in GLOSSARY_ZH))
    cat_zh = CATEGORY_ZH.get(rec.get("category", ""))
    if cat_zh and cat_zh not in zh:
        zh.append(cat_zh)
    return {
        "id": rec["id"],
        "title": rec.get("title", ""),
        "category": rec.get("category", ""),
        "source": rec.get("source", ""),
        "license_id": rec["license_id"],
        "keywords_en": kw,
        "keywords_zh": zh,
        "paths": pre.get("paths", 0),
        "bytes": pre.get("bytes", 0),
        "size_mm": pre.get("size_mm"),
        "quality": (pre.get("quality") or {}).get("verdict"),
    }


def build_index(entries: list[dict], generated_at: str) -> dict:
    return {
        "version": 1,
        "generated_at": generated_at,
        "count": len(entries),
        "icons": sorted(entries, key=lambda e: e["id"]),
    }
