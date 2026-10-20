"""JSONL 导出：alpaca（LLaMA-Factory）+ Dify 双模板。

- alpaca：{"instruction": breadcrumb 拼接, "input": "", "output": chunk content}
- dify：{"question": 章节标题/面包屑末级, "answer": chunk content}
- 章节勾选过滤：sections 为面包屑路径（"/" 连接），chunk 的面包屑
  路径以任一勾选项开头即纳入；为空/None 表示全部
- AI 思维链清洗（strip_cot）：AI 对话存档类文档（如 DeepSeek 问答记录）
  自带「【xx 推理过程】…【xx 回复】」结构，导出前可剥离推理段落只留回复
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from app.core.types import Chunk

logger = logging.getLogger(__name__)

EXPORT_FORMATS = ("alpaca", "dify")

# 面包屑路径连接符（与前端章节树勾选值口径一致）
SECTION_SEP = "/"


def breadcrumb_path(chunk: Chunk) -> str:
    """chunk 的章节路径（" / " 拼接，供章节勾选匹配）。"""
    return SECTION_SEP.join(chunk.breadcrumb)


def match_sections(chunk: Chunk, sections: list[str] | None) -> bool:
    """章节勾选过滤：任一选中路径为 chunk 面包屑路径前缀即命中。"""
    if not sections:
        return True
    path = breadcrumb_path(chunk)
    for section in sections:
        normalized = section.strip(SECTION_SEP)
        if not normalized:
            return True
        if path == normalized or path.startswith(normalized + SECTION_SEP):
            return True
        # 无标题文档（path 为空）只在全选时纳入
    return False


# AI 对话存档的思维链标记行（独立成行）：【xx 推理过程】/【xx 回复】。
# xx 限 1-24 字且不含方括号，避免误匹配正常长句；分隔行「====…」紧邻
# 标记行时随标记一并删除。
_COT_START_RE = re.compile(r"^【[^【】\n]{1,24}推理过程】$")
_COT_END_RE = re.compile(r"^【[^【】\n]{1,24}回复】$")
_COT_SEP_RE = re.compile(r"^={10,}\s*$")


def document_flow_order(chunks: list[Chunk]) -> list[Chunk]:
    """按原文文档流重建 chunk 顺序（多文档按 document_id 分组各自重建）。

    为什么需要：chunk_index 存在两套编号空间——顶层（父块+独立块）一套、
    子块一套，直接按 chunk_index 混排会交错错乱（前端 useChunks
    .sortByDocumentFlow 的同款问题）。正确顺序 = 顶层按 chunk_index 排，
    父块位置处展开其子块（子块按 chunk_index）。

    调用方需保证 Chunk.parent_id 已填充（导出查询有 parent_id 列）。
    """
    by_doc: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.document_id, []).append(c)
    ordered: list[Chunk] = []
    for group in by_doc.values():
        children_of: dict[str, list[Chunk]] = {}
        for c in group:
            if c.parent_id:
                children_of.setdefault(c.parent_id, []).append(c)
        top = [c for c in group if not c.parent_id]
        for t in sorted(top, key=lambda c: c.chunk_index):
            subs = children_of.get(t.id)
            if subs:
                # 父块位置处展开子块（父块自身是否导出由调用方过滤）
                ordered.extend(sorted(subs, key=lambda c: c.chunk_index))
                ordered.append(t)
            else:
                ordered.append(t)
    return ordered


def strip_cot_from_chunks(chunks: list[Chunk]) -> tuple[list[Chunk], int]:
    """剥离 AI 对话存档的思维链段落，仅保留回复正文。

    保守原则：宁可不删也不错删——
    - 只有先出现「【xx 推理过程】」才开始丢弃，孤立的「【xx 回复】」不动；
    - 只删除思维链区间内的行与紧邻标记的分隔行（允许隔空行），其余原样；
    - 不含任何标记的文档逐字节不变，且不计入清洗数。

    状态机跨同文档 chunk 传递（思维链可能横跨多块），换文档重置。
    调用前请用 document_flow_order 保证文档内顺序正确（乱序会让状态机
    在回复标记之后误入推理区间错删正文）。

    Returns:
        (清洗后 chunks, 被剔除或内容被修改的 chunk 数)。
    """
    # 按文档分组（保序）：每组先扫一遍「是否出现过推理起始标记」。
    # 文档级信号用于放宽孤立「回复」标记的消费条件——切分 overlap 前缀
    # 会让下一 chunk 重复上一 chunk 尾部的已消费标记，若不消费就会残留
    # 整段思维链；文档从未出现起始标记时孤立回复仍不动（普通文档零影响）。
    by_doc: dict[str, list[Chunk]] = {}
    order: list[str] = []
    for c in chunks:
        if c.document_id not in by_doc:
            by_doc[c.document_id] = []
            order.append(c.document_id)
        by_doc[c.document_id].append(c)

    out: list[Chunk] = []
    touched = 0
    for doc_id in order:
        group = by_doc[doc_id]
        doc_has_start = any(
            _COT_START_RE.match(line.strip())
            for c in group
            for line in c.content.split("\n")
        )
        in_cot = False
        in_skip_tail = False  # 刚消费标记：其后空行/一个分隔行随标记删除
        for chunk in group:
            lines = chunk.content.split("\n")
            kept: list[str] = []
            changed = False
            # pending = 暂存组「分隔行 + 其后空行」（真实文档的标记与分隔线
            # 之间常隔空行）；其下一个非空行若是被消费的标记则整组随标记
            # 删除，否则整组放行。不跨 chunk 传递——跨块残留的孤立分隔行
            # 无害，宁可多留也不错删（in_cot/in_skip_tail 跨块传递，语义核心）。
            pending: list[str] = []
            for line in lines:
                stripped = line.strip()
                is_start = bool(_COT_START_RE.match(stripped))
                is_end = bool(_COT_END_RE.match(stripped))
                # 消费条件：推理起始；或「回复」且正处于推理区间；
                # 或「回复」且文档级已出现起始标记（overlap 前缀重复
                # 标记的场景——其前内容一并丢弃）
                if is_start or (is_end and (in_cot or doc_has_start)):
                    if is_end and not in_cot:
                        # 孤立回复：块首至标记视为思维链区间（overlap 残余）
                        kept.clear()
                    pending.clear()
                    in_skip_tail = True
                    in_cot = is_start
                    changed = True
                    continue
                if in_cot:
                    # 思维链区间：整行丢弃（含区间内分隔行与空行）
                    pending.clear()
                    changed = True
                    continue
                if in_skip_tail:
                    # 标记后紧随（隔空行）的分隔行随标记删除
                    if stripped == "":
                        changed = True
                        continue
                    in_skip_tail = False
                    if _COT_SEP_RE.match(stripped):
                        changed = True
                        continue
                if stripped == "" and pending:
                    pending.append(line)
                    continue
                if _COT_SEP_RE.match(stripped):
                    if pending:
                        kept.extend(pending)
                    pending = [line]
                    continue
                if pending:
                    kept.extend(pending)
                    pending.clear()
                kept.append(line)
            if pending:
                kept.extend(pending)
            if not changed:
                out.append(chunk)
                continue
            touched += 1
            content = "\n".join(kept).strip()
            if content:
                out.append(replace(chunk, content=content))
            # 仅剩空白的 chunk 整条剔除，不留空 output
    return out, touched


def _to_alpaca(chunk: Chunk) -> dict:
    return {
        "instruction": " > ".join(chunk.breadcrumb),
        "input": "",
        "output": chunk.content,
    }


def _to_dify(chunk: Chunk) -> dict:
    question = chunk.heading_text or (
        chunk.breadcrumb[-1] if chunk.breadcrumb else "上下文片段"
    )
    return {"question": question, "answer": chunk.content}


class Exporter:
    """JSONL 导出器。"""

    def __init__(self, export_dir: str | Path) -> None:
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def export_jsonl(
        self,
        chunks: list[Chunk],
        fmt: str = "alpaca",
        output_path: str | Path | None = None,
        sections: list[str] | None = None,
    ) -> tuple[Path, int]:
        """导出 chunk 列表为 JSONL。

        Args:
            chunks: 待导出 chunk（通常含父+子，由调用方决定口径）。
            fmt: alpaca / dify。
            output_path: 输出路径；None 时自动生成到 export_dir。
            sections: 章节勾选过滤（面包屑路径列表）。

        Returns:
            (文件路径, 行数)。
        """
        if fmt not in EXPORT_FORMATS:
            raise ValueError(f"不支持的导出模板：{fmt}（可选 {EXPORT_FORMATS}）")
        converter = _to_alpaca if fmt == "alpaca" else _to_dify

        selected = [c for c in chunks if match_sections(c, sections)]

        if output_path is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            output_path = (
                self.export_dir / f"export_{fmt}_{stamp}_{uuid.uuid4().hex[:6]}.jsonl"
            )
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        with target.open("w", encoding="utf-8") as fh:
            for chunk in selected:
                fh.write(json.dumps(converter(chunk), ensure_ascii=False) + "\n")
        logger.info("导出完成：%s（%d 行，模板 %s）", target, len(selected), fmt)
        return target, len(selected)
