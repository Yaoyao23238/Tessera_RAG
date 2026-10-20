"""解析质量检测（解析预览页质量面板的数据源）。

纯函数模块：输入解析 Block 流，输出统计摘要 + 质量警告列表。
core 层零外部依赖，可独立单测。

警告类型（4 条，ARCHITECTURE 之外的增量能力）：
- no_heading：未识别到标题 → 切分无法按章节组织
- short_blocks：超短块过多 / 每页字符极少 → 疑似扫描件 OCR 失败或解析碎裂
- repeated_lines：跨页重复行（Docling 未识别的页眉页脚残留）→ 建议 LLM 清洗
- garbled_text：高乱码率（U+FFFD 替换符 / C0 控制字符）→ 建议换 OCR 或重扫
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.tokenizer import count_tokens
from app.core.types import Block, BlockType

# ---------------------------------------------------------------------------
# 检测阈值
# ---------------------------------------------------------------------------

_SHORT_BLOCK_CHARS = 10  # 正文/列表块低于此字符数计为超短块
_SHORT_BLOCK_RATIO = 0.3  # 超短块占比阈值
_SHORT_BLOCK_MIN_COUNT = 20  # 超短块绝对数量下限（小文档不误报）
_LOW_CHARS_PER_PAGE = 50  # 每页平均字符低于此值疑似 OCR 失败（与 parser 扫描件阈值一致）
_MIN_BLOCKS_FOR_NO_HEADING = 10  # 块数太少的文档不报「无标题」
_REPEAT_MIN_PAGES = 6  # 页数太少不做页眉页脚残留检测
_REPEAT_PAGE_RATIO = 0.5  # 重复文本覆盖页数比例阈值
_REPEAT_MIN_OCCURRENCES = 3  # 重复文本至少出现的页数
_REPEAT_MIN_CHARS = 4  # 参与检测的首尾块最小字符数
_GARBLED_WARN_RATIO = 0.005  # 乱码率 warning 阈值
_GARBLED_DANGER_RATIO = 0.02  # 乱码率 danger 阈值

# LLM 清洗 token 预估的 prompt 开销系数（指令 + system prompt 粗估）
_EST_PROMPT_OVERHEAD = 1.15

_WS_RE = re.compile(r"\s+")
# 常见页码模式（归一化时剥离，避免同一页眉因页码不同而漏检）
_PAGE_NO_RES = [
    re.compile(r"第\s*\d{1,4}\s*页"),  # 第N页
    re.compile(r"\d{1,4}\s*[/|]\s*\d{1,4}"),  # N/M
    re.compile(r"[\s\-—_·]*\d{1,4}\s*$"),  # 末尾孤立数字
    re.compile(r"^\s*\d{1,4}[\s\-—_·]*"),  # 开头孤立数字
]
# U+FFFD 替换符 + C0 控制字符（排除 \t \n \r）
_GARBLED_RE = re.compile(r"[�\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass
class QualityWarning:
    """单条质量警告。action 供前端挂一键操作。"""

    kind: str  # no_heading / short_blocks / repeated_lines / garbled_text
    severity: str  # info / warning / danger
    message: str
    suggestion: str
    action: str = ""  # open_clean_dialog / reparse / ""（仅展示）
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "suggestion": self.suggestion,
            "action": self.action,
            "detail": self.detail,
        }


@dataclass
class ParseReport:
    """解析质量报告：统计摘要 + 警告列表 + LLM 清洗 token 预估。"""

    total_blocks: int = 0
    total_chars: int = 0
    page_count: int = 0
    type_counts: dict[str, int] = field(default_factory=dict)
    est_tokens_text: int = 0  # 文本清洗档预估输入 token（含 prompt 开销粗估）
    est_tokens_problem: int = 0  # 仅问题块档预估输入 token
    est_tokens_table: int = 0  # 表格增强档预估输入 token
    warnings: list[QualityWarning] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_blocks": self.total_blocks,
            "total_chars": self.total_chars,
            "page_count": self.page_count,
            "type_counts": self.type_counts,
            "est_tokens_text": self.est_tokens_text,
            "est_tokens_problem": self.est_tokens_problem,
            "est_tokens_table": self.est_tokens_table,
            "warnings": [w.to_dict() for w in self.warnings],
        }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def compute_parse_report(blocks: list[Block]) -> ParseReport:
    """对解析 Block 流做质量体检，返回统计 + 警告。"""
    report = ParseReport()
    report.total_blocks = len(blocks)
    report.page_count = max((b.end_page for b in blocks), default=0)
    type_counts: dict[str, int] = {}
    for block in blocks:
        type_counts[block.type.value] = type_counts.get(block.type.value, 0) + 1
    report.type_counts = type_counts
    report.total_chars = sum(len(b.text) for b in blocks)

    # token 预估：文本清洗档处理 标题/正文/列表；表格增强档处理表格 markdown
    text_tokens = sum(
        count_tokens(b.text)
        for b in blocks
        if b.type in (BlockType.HEADING, BlockType.PARAGRAPH, BlockType.LIST_ITEM)
    )
    report.est_tokens_text = int(text_tokens * _EST_PROMPT_OVERHEAD)
    table_tokens = sum(
        count_tokens((b.table_data or {}).get("markdown") or b.text)
        for b in blocks
        if b.type == BlockType.TABLE
    )
    report.est_tokens_table = int(table_tokens * _EST_PROMPT_OVERHEAD)
    problem_tokens = sum(
        count_tokens(blocks[i].text)
        for i in find_problem_block_indexes(blocks)
        if blocks[i].type
        in (BlockType.HEADING, BlockType.PARAGRAPH, BlockType.LIST_ITEM)
    )
    report.est_tokens_problem = int(problem_tokens * _EST_PROMPT_OVERHEAD)

    for detector in (
        _detect_no_heading,
        _detect_short_blocks,
        _detect_repeated_lines,
        _detect_garbled,
    ):
        warning = detector(blocks, report)
        if warning is not None:
            report.warnings.append(warning)
    return report


# ---------------------------------------------------------------------------
# 各警告检测器（独立纯函数，便于单测）
# ---------------------------------------------------------------------------


def _detect_no_heading(
    blocks: list[Block], report: ParseReport
) -> QualityWarning | None:
    """无标题文档：块数达到规模但没有任何 heading。"""
    if report.total_blocks < _MIN_BLOCKS_FOR_NO_HEADING:
        return None
    if report.type_counts.get(BlockType.HEADING.value, 0) > 0:
        return None
    return QualityWarning(
        kind="no_heading",
        severity="info",
        message="未识别到任何标题，切分将无法按章节组织",
        suggestion="已按数字序号与常见章节名自动推断标题；若仍无标题，说明原文档缺少可识别的章节结构，切分将按正文流组织",
        action="",
    )


def _detect_short_blocks(
    blocks: list[Block], report: ParseReport
) -> QualityWarning | None:
    """超短块过多 / 每页字符极少（疑似 OCR 失败或解析碎裂）。"""
    candidates = [
        b
        for b in blocks
        if b.type in (BlockType.PARAGRAPH, BlockType.LIST_ITEM)
    ]
    short_count = sum(
        1 for b in candidates if len(b.text.strip()) < _SHORT_BLOCK_CHARS
    )
    pages = max(report.page_count, 1)
    # 与 parser._looks_scanned 同口径：非图片块文本 / 页数
    text_chars = sum(len(b.text) for b in blocks if b.type != BlockType.IMAGE)
    chars_per_page = text_chars / pages

    if chars_per_page < _LOW_CHARS_PER_PAGE:
        return QualityWarning(
            kind="short_blocks",
            severity="danger",
            message=f"每页平均仅 {int(chars_per_page)} 字符，疑似扫描件 OCR 失败",
            suggestion="建议重新解析（自动重试 OCR）；若仍失败请换用更清晰的扫描件",
            action="reparse",
            detail={"chars_per_page": round(chars_per_page, 1)},
        )
    if (
        len(candidates) > 0
        and short_count >= _SHORT_BLOCK_MIN_COUNT
        and short_count / len(candidates) >= _SHORT_BLOCK_RATIO
    ):
        return QualityWarning(
            kind="short_blocks",
            severity="warning",
            message=f"超短块占比 {short_count}/{len(candidates)}，文本可能解析碎裂",
            suggestion="可用 LLM 清洗智能合并断行、修复碎裂文本",
            action="open_clean_dialog",
            detail={
                "short_count": short_count,
                "candidate_count": len(candidates),
                "ratio": round(short_count / len(candidates), 3),
            },
        )
    return None


def _normalize_edge_text(text: str) -> str:
    """页眉页脚候选文本归一化：小写化 → 剥页码模式 → 去空白。"""
    normalized = text.lower()
    for pattern in _PAGE_NO_RES:
        normalized = pattern.sub("", normalized)
    return _WS_RE.sub("", normalized)


def _repeated_edge_keys(blocks: list[Block], page_count: int) -> dict[str, tuple[set[int], str]]:
    """页眉页脚残留检测共用逻辑：返回 {归一化 key: (出现页集合, 示例原文)}。

    parser 已过滤 Docling 标记的 page_header/page_footer，此处检测漏网者：
    每页首个/最后一个正文块归一化后，同一文本覆盖 >= max(3, 50%页数) 判残留。
    仅统计正文类块（heading 重复有章节语义，不参与）。
    """
    if page_count < _REPEAT_MIN_PAGES:
        return {}
    by_page: dict[int, list[Block]] = {}
    for block in blocks:
        if block.type != BlockType.PARAGRAPH:
            continue
        by_page.setdefault(block.page, []).append(block)

    occurrences: dict[str, tuple[set[int], str]] = {}
    for page, page_blocks in by_page.items():
        for candidate in (page_blocks[0], page_blocks[-1]):
            raw = candidate.text.strip()
            if len(raw) < _REPEAT_MIN_CHARS:
                continue
            key = _normalize_edge_text(raw)
            if not key:
                continue
            entry = occurrences.setdefault(key, (set(), raw))
            entry[0].add(page)

    threshold = max(_REPEAT_MIN_OCCURRENCES, page_count * _REPEAT_PAGE_RATIO)
    return {
        key: value
        for key, value in occurrences.items()
        if len(value[0]) >= threshold
    }


def remove_repeated_edges(blocks: list[Block]) -> list[Block]:
    """自动剔除跨页重复的页眉页脚残留（复用 _repeated_edge_keys 检测）。

    仅删除「每页首个/最后一个、且文本与跨页重复 key 一致」的正文块，
    与检测逻辑保持同一口径，避免误删正文中的正常重复内容。
    无重复时原样返回（不新建列表，保持引用）。
    """
    page_count = max((b.end_page for b in blocks), default=0)
    repeated_keys = set(_repeated_edge_keys(blocks, page_count))
    if not repeated_keys:
        return blocks
    by_page: dict[int, list[int]] = {}
    for idx, block in enumerate(blocks):
        by_page.setdefault(block.page, []).append(idx)
    remove: set[int] = set()
    for page, indexes in by_page.items():
        if not indexes:
            continue
        for idx in (indexes[0], indexes[-1]):
            block = blocks[idx]
            if block.type != BlockType.PARAGRAPH:
                continue
            raw = block.text.strip()
            if len(raw) < _REPEAT_MIN_CHARS:
                continue
            key = _normalize_edge_text(raw)
            if key and key in repeated_keys:
                remove.add(idx)
    if not remove:
        return blocks
    return [b for i, b in enumerate(blocks) if i not in remove]


def find_problem_block_indexes(blocks: list[Block]) -> set[int]:
    """识别「问题块」下标集合（LLM 清洗 problem 档的处理范围）：

    - 超短正文/列表块（解析碎裂）
    - 含乱码字符的块（U+FFFD / C0 控制字符）
    - 跨页重复的首/尾正文块（页眉页脚残留）
    """
    page_count = max((b.end_page for b in blocks), default=0)
    repeated_keys = set(_repeated_edge_keys(blocks, page_count))
    problem: set[int] = set()
    for idx, block in enumerate(blocks):
        if block.type not in (
            BlockType.HEADING,
            BlockType.PARAGRAPH,
            BlockType.LIST_ITEM,
        ):
            continue
        text = block.text.strip()
        if not text:
            continue
        if (
            block.type in (BlockType.PARAGRAPH, BlockType.LIST_ITEM)
            and len(text) < _SHORT_BLOCK_CHARS
        ):
            problem.add(idx)
            continue
        if _GARBLED_RE.search(block.text):
            problem.add(idx)
            continue
        if repeated_keys and _normalize_edge_text(text) in repeated_keys:
            problem.add(idx)
    return problem


def _detect_repeated_lines(
    blocks: list[Block], report: ParseReport
) -> QualityWarning | None:
    """跨页重复的首/尾正文块警告（检测逻辑见 _repeated_edge_keys）。"""
    repeated_map = _repeated_edge_keys(blocks, report.page_count)
    if not repeated_map:
        return None
    repeated = sorted(
        ((key, pages, sample) for key, (pages, sample) in repeated_map.items()),
        key=lambda item: len(item[1]),
        reverse=True,
    )
    examples = [sample for _, _, sample in repeated[:5]]
    worst = len(repeated[0][1])
    return QualityWarning(
        kind="repeated_lines",
        severity="warning",
        message=f"检测到 {len(repeated)} 组跨页重复文本（疑似页眉页脚残留），最高覆盖 {worst}/{report.page_count} 页",
        suggestion="建议用 LLM 清洗自动去除页眉页脚",
        action="open_clean_dialog",
        detail={"count": len(repeated), "max_pages": worst, "examples": examples},
    )


def _detect_garbled(
    blocks: list[Block], report: ParseReport
) -> QualityWarning | None:
    """高乱码率：U+FFFD 替换符 + C0 控制字符占比。"""
    if report.total_chars == 0:
        return None
    bad = sum(len(_GARBLED_RE.findall(b.text)) for b in blocks)
    ratio = bad / report.total_chars
    if ratio >= _GARBLED_DANGER_RATIO:
        severity = "danger"
    elif ratio >= _GARBLED_WARN_RATIO:
        severity = "warning"
    else:
        return None
    return QualityWarning(
        kind="garbled_text",
        severity=severity,
        message=f"乱码字符占比 {ratio:.1%}，文本可能存在编码或 OCR 问题",
        suggestion="建议重新解析（重试 OCR）；若为扫描件请换更清晰的源文件",
        action="reparse",
        detail={"garbled_chars": bad, "ratio": round(ratio, 4)},
    )
