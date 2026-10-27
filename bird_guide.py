#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bird_guide.py — 鸟类图鉴数据库查询层。

数据文件：data/bird_taxonomy.csv
字段：中文名, 拉丁名, 目, 科, 属, 是否雌雄异形, 羽色阶段描述, 图鉴图文件名

图鉴图存放：data/gallery/{gallery_file}
你可以自己把图鉴图丢进 gallery/ 目录，文件名和 CSV 里的 gallery_file 对应即可。
识别完成后程序会自动查图鉴，展示雌雄异形提示和羽色描述。
"""

from __future__ import annotations

import csv
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from toolpaths import app_dir


@dataclass
class BirdGuideEntry:
    common_name_zh: str
    scientific_name: str
    order: str
    family: str
    genus: str
    sexually_dimorphic: bool
    plumage_notes: str
    gallery_file: str

    @property
    def gallery_path(self) -> Optional[Path]:
        """返回图鉴图绝对路径，不存在则 None。"""
        if not self.gallery_file:
            return None
        p = app_dir() / "data" / "gallery" / self.gallery_file
        return p if p.exists() else None

    def has_gallery_image(self) -> bool:
        return self.gallery_path is not None


class BirdGuide:
    """加载图鉴 CSV，提供中文名/拉丁名双向查询。"""

    def __init__(self, csv_path: Optional[Path] = None):
        if csv_path is None:
            csv_path = app_dir() / "data" / "bird_taxonomy.csv"
        self.csv_path = csv_path
        self._by_zh: dict[str, BirdGuideEntry] = {}
        self._by_latin: dict[str, BirdGuideEntry] = {}
        self._load()

    def _load(self):
        if not self.csv_path.exists():
            return
        seen_latin: set[str] = set()
        with self.csv_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                entry = BirdGuideEntry(
                    common_name_zh=row["common_name_zh"].strip(),
                    scientific_name=row["scientific_name"].strip(),
                    order=row["order"].strip(),
                    family=row["family"].strip(),
                    genus=row["genus"].strip(),
                    sexually_dimorphic=row["sexually_dimorphic"].strip().lower() == "true",
                    plumage_notes=row.get("plumage_notes", "").strip(),
                    gallery_file=row.get("gallery_file", "").strip(),
                )
                # 按拉丁名去重（CSV 里可能有重复行）
                if entry.scientific_name in seen_latin:
                    continue
                seen_latin.add(entry.scientific_name)
                self._by_zh[entry.common_name_zh] = entry
                self._by_latin[entry.scientific_name.lower()] = entry

    def lookup_by_zh(self, name_zh: str) -> Optional[BirdGuideEntry]:
        return self._by_zh.get(name_zh.strip())

    def lookup_by_latin(self, latin: str) -> Optional[BirdGuideEntry]:
        return self._by_latin.get(latin.strip().lower())

    def lookup(self, name: str) -> Optional[BirdGuideEntry]:
        """先按中文名查，查不到按拉丁名查。"""
        entry = self.lookup_by_zh(name)
        if entry is None:
            entry = self.lookup_by_latin(name)
        return entry

    def all_entries(self) -> list[BirdGuideEntry]:
        return list(self._by_zh.values())

    def __len__(self) -> int:
        return len(self._by_zh)

    def __repr__(self) -> str:
        return f"<BirdGuide entries={len(self)} csv={self.csv_path.name}>"


# 模块级单例，避免每次调用都重读 CSV
_default_guide: Optional[BirdGuide] = None


def get_guide() -> BirdGuide:
    global _default_guide
    if _default_guide is None:
        _default_guide = BirdGuide()
    return _default_guide


if __name__ == "__main__":
    # 自测
    g = get_guide()
    print(f"图鉴加载完成，共 {len(g)} 种鸟")
    for name in ["凤头鹰", "红隼", "普通翠鸟", "小白腰雨燕"]:
        e = g.lookup_by_zh(name)
        if e:
            print(f"\n【{e.common_name_zh}】{e.scientific_name}")
            print(f"  分类: {e.order} > {e.family} > {e.genus}")
            print(f"  雌雄异形: {'是' if e.sexually_dimorphic else '否'}")
            print(f"  羽色: {e.plumage_notes}")
            print(f"  图鉴图: {'有' if e.has_gallery_image() else '未提供（把图丢 data/gallery/ 即可）'}")
#（注：内容由AI生成）
