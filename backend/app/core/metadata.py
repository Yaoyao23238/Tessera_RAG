"""文档元数据提取（解析质量加固：title / author / lang 填充）。

纯函数模块，core 层零外部依赖，可独立单测。

- title：优先取首个标题块（level 1 优先），无标题取首个非空正文块
- lang：按文本字符集占比启发式判定（zh / ja / ko / en）
- author：优先取 Docling 元数据（PDF 内嵌作者），无则留空

策略：只做「可确定」的填充，把握不足时返回 None，不硬造元数据。
"""

from __future__ import annotations

import re

from app.core.types import Block, BlockType

# 各文字系统字符区间（用于语言启发式判定）
_HAN_RE = re.compile(r"[\u4e00-\u9fff]")          # CJK 汉字
_HIRAGANA_RE = re.compile(r"[\u3040-\u309f]")     # 平假名（日文）
_KATAKANA_RE = re.compile(r"[\u30a0-\u30ff]")     # 片假名（日文）
_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")       # 谚文（韩文）
_LATIN_RE = re.compile(r"[A-Za-z]")


def detect_language(text: str) -> str | None:
    """按字符集占比判定语言。把握不足（无从属文本）返回 None。"""
    if not text:
        return None
    han = len(_HAN_RE.findall(text))
    kana = len(_HIRAGANA_RE.findall(text)) + len(_KATAKANA_RE.findall(text))
    hangul = len(_HANGUL_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    cjk = han + kana + hangul
    total = cjk + latin
    if total == 0:
        return None
    if hangul / total > 0.3:
        return "ko"
    if kana / total > 0.3:
        return "ja"
    if han / total > 0.3:
        return "zh"
    if latin / total > 0.5:
        return "en"
    return None


def extract_title(blocks: list[Block]) -> str | None:
    """从 Block 流提取文档标题：首个 level1 标题，否则首个标题，否则首个非空正文。"""
    for block in blocks:
        if block.type == BlockType.HEADING and block.level == 1:
            text = block.text.strip()
            if text:
                return text
    for block in blocks:
        if block.type == BlockType.HEADING:
            text = block.text.strip()
            if text:
                return text
    for block in blocks:
        if block.type in (BlockType.PARAGRAPH, BlockType.LIST_ITEM):
            text = block.text.strip()
            # 标题通常较短，取首个有实际内容的块
            if text and len(text) <= 200:
                return text
    return None


def extract_metadata(
    blocks: list[Block], docling_meta: dict | None = None
) -> dict:
    """合并规则提取与 Docling 元数据，返回 {title, author, lang} 字典（缺省 None）。"""
    docling_meta = docling_meta or {}
    meta: dict = {"title": None, "author": None, "lang": None}

    title = extract_title(blocks) or (docling_meta.get("title") or None)
    if title:
        meta["title"] = title

    author = docling_meta.get("author") or None
    if author:
        meta["author"] = author

    sample = " ".join(b.text for b in blocks if b.text)
    lang = detect_language(sample)
    if lang:
        meta["lang"] = lang
    return meta