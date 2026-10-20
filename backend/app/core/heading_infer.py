"""规则标题推断（heading inference）。

Docling 对未使用 Word 标题样式、手动排版（加粗/字号）的文档，会把标题
识别为普通段落，导致切分无法按章节组织。本模块用规则从段落文本推断标题：

- 数字序号标题：「1 引言」「2.1 大语言模型」「2.1.3 xxx」→ level = 段数
- 无序号章节名：「摘要」「参考文献」「结论」等（允许空白变体）→ level 1
- 自动跳过目录区（「目录」标记后的连续短行条目），避免与正文标题重复

另含 `refine_heading_levels`：对 Docling 已判为 HEADING 的块细化层级
（Docling 给的 level 恒为 1）。已判标题类型确定，规则比段落推断宽松：
- 数字序号允许紧跟 CJK（OCR 常丢空格：「1范围」「4著录项目」）→ level = 段数
- 中文序号「一、」「二、」→ level 2；「第X章」→ 1、「第X节」→ 2
- 无序号标题保持原层级（术语条目、栏目名等无法从文本可靠分级，需字号信息）

纯函数、无损（只提升类型与层级，不改文本），可在解析 / 清洗结果上重复应用。
"""

from __future__ import annotations

import re

from app.core.types import Block, BlockType

#: 数字序号标题：「1 引言」/「2.1 xxx」/「2.1.3 xxx」（最多三级）
_NUM_HEADING_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,2})\s+(\S)")
#: 序号后紧跟这些量词/单位字 → 非标题（「23 级人工智能」「1 年规划」「1.5 倍速」）
_UNIT_START_WORDS = {
    "级", "年", "月", "日", "时", "分", "秒", "章", "节", "张", "期", "版", "号", "名", "位", "倍",
}
#: 无序号章节名（归一化空白后全等匹配）
_SECTION_NAMES = {
    "摘要",
    "引言",
    "目录",
    "结论",
    "总结",
    "致谢",
    "参考文献",
    "参考资料",
    "后记",
    "附录",
    "索引",
}

#: 标题行最大长度（超过视为正文，防误判如「23 级人工智能」）
_MAX_HEADING_CHARS = 40
#: 正文句读标点：标题行不应包含（防「2023 年，我国…」式误判）
_BODY_PUNCT_RE = re.compile(r"[。，；：？！、]")
#: 目录条目长度上限：目录区内的短行均视为条目跳过推断，
#: 遇到超过此长度的段落视为进入正文，恢复推断
_TOC_ENTRY_MAX_CHARS = 30

_WS_RE = re.compile(r"\s+")

#: 已判标题的层级细化：数字序号（允许无空格紧跟 CJK：「1范围」「4.1 专著」）
_NUM_LEVEL_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,2})(\s*)(\S)")
#: 中文序号「一、」「二、」→ level 2（刊物/会议分级等小节标题）
_CN_ORDINAL_RE = re.compile(r"^[一二三四五六七八九十]{1,3}、")
#: 「第X章」→ level 1、「第X节」→ level 2
_CN_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百千\d]+章")
_CN_SECTION_RE = re.compile(r"^第[一二三四五六七八九十百千\d]+节")
_CJK_RE = re.compile(r"[一-鿿]")


def _norm(text: str) -> str:
    return _WS_RE.sub("", text)


def infer_headings(blocks: list[Block]) -> list[Block]:
    """对 Block 流做标题推断，原地修改并返回同一列表。"""
    in_toc = False
    pending: tuple[Block, int] | None = None  # 目录区最后一条疑似标题（正文标题候补）
    for block in blocks:
        if block.type is not BlockType.PARAGRAPH:
            continue
        text = block.text.strip()
        if not text:
            continue

        norm = _norm(text)
        # 「目录」标记：进入目录区（本身不当标题，避免吞掉目录条目）
        if norm == "目录":
            in_toc = True
            continue
        if in_toc:
            # 目录区：条目为短行（<= 30 字符），全部跳过；超过即进入正文。
            # 进入正文时回看 pending：正文标题通常紧跟正文首段之前，
            # 恢复点处的长段落说明 pending 是正文标题而非目录条目。
            if len(text) > _TOC_ENTRY_MAX_CHARS:
                in_toc = False
                _promote_pending(pending)
                pending = None
            else:
                level = _looks_like_heading(text, norm)
                if level is not None:
                    pending = (block, level)
                continue

        if _promote_if_heading(block, text, norm):
            continue
    refine_heading_levels(blocks)
    return blocks


def refine_heading_levels(blocks: list[Block]) -> list[Block]:
    """细化已判 HEADING 块的层级（原地修改并返回同一列表）。

    Docling 对 PDF 标题给的 level 恒为 1（质量检查 P1：96+48 个标题全 L1）。
    类型已由解析器确定，此处只按序号形式重算层级，不改类型：
    - 数字序号（空格可有可无，无空格时要求紧跟 CJK 且非量词字）→ level = 段数
    - 「一、」中文序号 → 2；「第X章」→ 1；「第X节」→ 2
    - 其余保持原层级
    """
    for block in blocks:
        if block.type is not BlockType.HEADING:
            continue
        text = block.text.strip()
        if not text:
            continue
        m = _NUM_LEVEL_RE.match(text)
        if m:
            num, ws, nxt = m.group(1), m.group(2), m.group(3)
            # 无空格时只接受紧跟 CJK（防「1.5x」「2023」之类误分级）
            if (ws or _CJK_RE.match(nxt)) and nxt not in _UNIT_START_WORDS:
                block.level = num.count(".") + 1
                continue
        if _CN_ORDINAL_RE.match(text) or _CN_SECTION_RE.match(text):
            block.level = 2
        elif _CN_CHAPTER_RE.match(text):
            block.level = 1
    return blocks


def _looks_like_heading(text: str, norm: str) -> int | None:
    """疑似标题 → 返回应推断的层级；否则 None（不改块）。"""
    if norm in _SECTION_NAMES and norm != "目录":
        return 1
    if len(text) <= _MAX_HEADING_CHARS and not _BODY_PUNCT_RE.search(text):
        m = _NUM_HEADING_RE.match(text)
        if m and m.group(2) not in _UNIT_START_WORDS:
            return m.group(1).count(".") + 1
    return None


def _promote_pending(pending: tuple[Block, int] | None) -> None:
    if pending is None:
        return
    _promote(pending[0], pending[1])


def _promote_if_heading(block: Block, text: str, norm: str) -> bool:
    """尝试提升为标题；提升成功返回 True。"""
    level = _looks_like_heading(text, norm)
    if level is None:
        return False
    _promote(block, level)
    return True


def _promote(block: Block, level: int) -> None:
    block.type = BlockType.HEADING
    block.level = level
    block.list_level = 0
