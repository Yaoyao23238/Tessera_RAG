"""Docling 解析封装。

将 PDF / DOCX / PPTX / MD / TXT / HTML 统一解析为 Block 列表。
docling 为重型依赖，import 处做懒加载，保证 core 层可独立测试。

错误处理（ARCHITECTURE 第十三章）：
- 加密 PDF：抛 EncryptedPDFError，状态机置 encrypted，密码不落盘
- 扫描件（每页 < 50 字）：自动启用 OCR 重试
- 空内容：抛 EmptyDocumentError
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from app.core.heading_infer import infer_headings
from app.core.quality import remove_repeated_edges
from app.core.types import Block, BlockType

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".md", ".txt", ".html"}

# 扫描件判定阈值：平均每页字符数
_SCANNED_PAGE_CHAR_THRESHOLD = 50

# PDF 分批转换每批页数（docling-parse 一次性处理多页会 std::bad_alloc）
_PDF_BATCH_SIZE = 10


class ParserError(Exception):
    """解析失败基类。"""


class EncryptedPDFError(ParserError):
    """加密 PDF（等待用户提交密码）。"""


class EmptyDocumentError(ParserError):
    """未检测到文本内容。"""


class UnsupportedFormatError(ParserError):
    """不支持的文件格式。"""


# Docling 标签 → BlockType 映射（懒匹配，避免硬依赖枚举）
_LABEL_MAP: dict[str, BlockType] = {
    "title": BlockType.HEADING,
    "section_header": BlockType.HEADING,
    "paragraph": BlockType.PARAGRAPH,
    "text": BlockType.PARAGRAPH,
    "table": BlockType.TABLE,
    "code": BlockType.CODE,
    "picture": BlockType.IMAGE,
    "list_item": BlockType.LIST_ITEM,
    "formula": BlockType.PARAGRAPH,
    "caption": BlockType.PARAGRAPH,
    "footnote": BlockType.PARAGRAPH,
}
_SKIP_LABELS = {"page_header", "page_footer"}  # 页眉页脚过滤


def _clean_table_cells(
    headers: list[str], rows: list[list[str]]
) -> tuple[list[str], list[list[str]]]:
    """表格单元格清洗：去首尾空白与 docx 加粗标记 `**`；表头全空则置空表头。"""
    headers = [h.replace("**", "").strip() for h in headers]
    rows = [[v.replace("**", "").strip() for v in row] for row in rows]
    if headers and not any(headers):
        headers = []
    return headers, rows


def _inherit_continuation_table_headers(blocks: list[Block]) -> None:
    """跨页续表表头继承（原地修改）。

    docling 按页切表，续表页常缺表头行，两种形态：
    - 未检出表头（headers=[]）→ 继承上一表格块表头
    - 首行数据被误判为表头（CCF p55 实测：表头是"17|IDA|…"）→ 降级回数据行再继承
    判据保守：两表格块页码相邻（gap<=1）、列数一致；误判降级额外要求
    表头首单元格与数据首行首单元格都是纯数字（序号列特征）。
    """
    prev: Block | None = None
    for block in blocks:
        if block.type != BlockType.TABLE or not block.table_data:
            continue
        td = block.table_data
        headers: list[str] = td.get("headers") or []
        rows: list[list[str]] = td.get("rows") or []
        ncols = len(headers) or (len(rows[0]) if rows else 0)
        if prev is not None:
            prev_headers: list[str] = (prev.table_data or {}).get("headers") or []
            prev_rows: list[list[str]] = (prev.table_data or {}).get("rows") or []
            prev_cols = len(prev_headers) or (len(prev_rows[0]) if prev_rows else 0)
            adjacent = 0 < block.page - prev.page <= 1
            if prev_headers and adjacent and ncols == prev_cols and ncols > 0:
                if not headers:
                    td["headers"] = list(prev_headers)
                elif (
                    headers != prev_headers
                    and headers[0].strip().isdigit()
                    and rows
                    and rows[0]
                    and rows[0][0].strip().isdigit()
                ):
                    td["rows"] = [list(headers), *rows]
                    td["headers"] = list(prev_headers)
        prev = block


class Parser:
    """Docling 封装：parse(file_path, password) -> list[Block]。"""

    def parse(self, file_path: str | Path, password: str | None = None) -> list[Block]:
        """解析文档为归一化 Block 流。

        Args:
            file_path: 文档路径（pathlib + UTF-8，兼容中文路径）。
            password: 加密 PDF 密码（不落盘）。

        Raises:
            UnsupportedFormatError: 不支持的扩展名。
            EncryptedPDFError: PDF 加密且密码缺失/错误。
            EmptyDocumentError: 解析结果无文本内容。
            ParserError: 其他解析失败。
        """
        path = Path(file_path)
        ext = path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise UnsupportedFormatError(f"不支持的格式：{ext}")
        blocks, _meta = self.parse_with_meta(path, password)
        return blocks

    def parse_with_meta(
        self, file_path: str | Path, password: str | None = None
    ) -> tuple[list[Block], dict]:
        """解析文档为归一化 Block 流，并返回提取的元数据 {title, author, lang}。

        与 parse() 同异常语义；元数据供 run_parse 填充 Document 行。
        """
        path = Path(file_path)
        ext = path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise UnsupportedFormatError(f"不支持的格式：{ext}")

        # 纯文本类直接读取，无需 Docling；元数据仍提取（标题/语言）
        from app.core.metadata import extract_metadata

        if ext in {".md", ".txt"}:
            blocks = self._parse_plain_text(path)
            infer_headings(blocks)
            meta = extract_metadata(blocks)
            return blocks, meta
        if ext == ".html":
            blocks = self._parse_html(path)
            infer_headings(blocks)
            meta = extract_metadata(blocks)
            return blocks, meta

        if ext == ".pdf":
            blocks, meta = self._parse_pdf(path, password)
        else:
            converter = self._build_converter(password=password, enable_ocr=False)
            doc = self._convert(converter, path, password).document
            blocks = self._doc_to_blocks(doc)
            meta = self._extract_docling_meta(doc)

        # 自动清理跨页重复的页眉页脚残留（Docling 漏标的，见 quality.remove_repeated_edges）
        blocks = remove_repeated_edges(blocks)
        # 跨页续表表头继承：必须在分批合并后的完整 Block 流上做
        # （PDF 分批转换每批 10 页，批内调用会漏掉跨批边界的续表）
        _inherit_continuation_table_headers(blocks)
        if not blocks:
            raise EmptyDocumentError("未检测到文本内容")
        # 规则标题推断：手动排版文档（未用标题样式）的标题会被 Docling
        # 识别为段落，此处按数字序号 / 章节名模式提升为 heading（见 heading_infer）
        infer_headings(blocks)
        # Docling 元数据（PDF 内嵌 title/author）可能为空，用 blocks 内容补充
        # 标题/语言（必须在 infer_headings 之后，否则推断标题取不到）
        meta = extract_metadata(blocks, docling_meta=meta)
        return blocks, meta

    # ------------------------------------------------------------------
    # 纯文本 / HTML 直通解析
    # ------------------------------------------------------------------

    def _read_text_auto(self, path: Path) -> str:
        """按编码自动检测读取文本文件。

        优先 UTF-8（utf-8-sig 自动剥离 BOM）；失败后严格尝试常见东亚编码
        （gb18030 超集 GBK/GB2312，覆盖中文简繁，再 big5/shift_jis/euc_kr）；
        仍失败再交给 charset_normalizer 兜底，最后回退 UTF-8 宽容解码。

        说明：charset_normalizer 对短中文文本易误判（如把 GBK 当 cp949），
        故东亚编码显式候选排在它之前，保证任何文本都能读出内容而不抛异常。
        """
        raw = path.read_bytes()
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
        text = self._decode_cjk(raw)
        if text is not None:
            return text
        try:
            from charset_normalizer import from_bytes

            best = from_bytes(raw).best()
            if best is not None and best.encoding:
                return raw.decode(best.encoding)
        except Exception:  # noqa: BLE001
            logger.debug("charset_normalizer 检测失败，回退 UTF-8 宽容解码", exc_info=True)
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _decode_cjk(raw: bytes) -> str | None:
        """严格尝试常见东亚编码，返回首段「干净」解码（无替换符/控制字符）。

        gb18030 是 GBK/GB2312 超集，覆盖中文简繁；随后 big5 / shift_jis /
        euc_kr 兜底。仅接受解码后无 U+FFFD 与控制字符的文本，避免宽容编码
        （如 gb18030）把其它编码的字节误吞成乱码。
        """
        for encoding in ("gb18030", "big5", "shift_jis", "euc_kr"):
            try:
                text = raw.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
            if any(
                ch == "\ufffd" or (ord(ch) < 32 and ch not in "\t\n\r")
                for ch in text
            ):
                continue
            return text
        return None

    def _parse_plain_text(self, path: Path) -> list[Block]:
        text = self._read_text_auto(path).strip()
        if not text:
            raise EmptyDocumentError("未检测到文本内容")
        blocks: list[Block] = []
        for raw in text.split("\n"):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                level = len(line) - len(line.lstrip("#"))
                blocks.append(
                    Block(
                        type=BlockType.HEADING,
                        text=line.lstrip("#").strip(),
                        level=min(level, 6),
                        page=1,
                    )
                )
            else:
                blocks.append(Block(type=BlockType.PARAGRAPH, text=line, page=1))
        return blocks

    def _parse_html(self, path: Path) -> list[Block]:
        """HTML 走 Docling（保持表格/标题结构）；Docling 不可用时退化纯文本。"""
        try:
            converter = self._build_converter(password=None, enable_ocr=False)
            doc = self._convert(converter, path, None).document
            blocks = self._doc_to_blocks(doc)
            if blocks:
                return blocks
        except ParserError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("Docling 解析 HTML 失败，退化为纯文本提取", exc_info=True)
        import re

        text = path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"<script.*?</script>", "", text, flags=re.DOTALL)
        text = re.sub(r"<style.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", "\n", text)
        blocks = [
            Block(type=BlockType.PARAGRAPH, text=line.strip(), page=1)
            for line in text.split("\n")
            if line.strip()
        ]
        if not blocks:
            raise EmptyDocumentError("未检测到文本内容")
        infer_headings(blocks)
        return blocks

    # ------------------------------------------------------------------
    # Docling 集成
    # ------------------------------------------------------------------

    def _build_converter(self, password: str | None, enable_ocr: bool) -> Any:
        """构建 DocumentConverter（懒导入 docling）。"""
        try:
            from docling.document_converter import DocumentConverter
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions,
                RapidOcrOptions,
            )
            from docling.document_converter import PdfFormatOption
        except ImportError as exc:
            raise ParserError(
                "docling 未安装，无法解析该格式（pip install docling）"
            ) from exc

        options = PdfPipelineOptions()
        if enable_ocr:
            options.do_ocr = True
            options.images_scale = 2.0
            options.ocr_options = RapidOcrOptions(
                lang=["chinese"], backend="onnxruntime"
            )
        else:
            options.do_ocr = False
        if password:
            setattr(options, "password", password)
        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=options)
            }
        )

    def _convert(
        self,
        converter: Any,
        path: Path,
        password: str | None,
        page_range: tuple[int, int] | None = None,
    ) -> Any:
        """执行转换，统一异常翻译。page_range 指定后只转换该页范围（1-based）。"""
        try:
            if page_range is not None:
                result = converter.convert(str(path), page_range=page_range)
            else:
                result = converter.convert(str(path))
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            if "encrypt" in message or "password" in message:
                raise EncryptedPDFError(
                    "PDF 已加密，需要密码" if not password else "密码错误"
                ) from exc
            raise ParserError(f"解析失败：{exc}") from exc
        return result

    # ------------------------------------------------------------------
    # PDF 分批解析（docling-parse 多页 std::bad_alloc 规避）
    # ------------------------------------------------------------------

    def _parse_pdf(
        self, path: Path, password: str | None
    ) -> tuple[list[Block], dict]:
        """PDF 解析：分批转换避免 docling-parse 多页内存累积导致 std::bad_alloc。

        第一遍 do_ocr=False（数字版 PDF 读文本层）；扫描件判定后用 OCR 重跑。
        """
        total_pages = self._pdf_page_count(path)
        if total_pages is None:
            return self._parse_pdf_legacy(path, password)

        blocks, meta = self._convert_pdf_batches(
            path, password, enable_ocr=False, total_pages=total_pages
        )
        if self._looks_scanned(blocks):
            logger.info("检测到疑似扫描件，启用 OCR 重新解析：%s", path.name)
            blocks, meta = self._convert_pdf_batches(
                path, password, enable_ocr=True, total_pages=total_pages
            )
        return blocks, meta

    def _parse_pdf_legacy(
        self, path: Path, password: str | None
    ) -> tuple[list[Block], dict]:
        """回退路径：一次性转换（获取页数失败时使用，旧行为）。"""
        converter = self._build_converter(password=password, enable_ocr=False)
        doc = self._convert(converter, path, password).document
        blocks = self._doc_to_blocks(doc)
        meta = self._extract_docling_meta(doc)
        if self._looks_scanned(blocks):
            logger.info("检测到疑似扫描件，启用 OCR 重新解析：%s", path.name)
            converter = self._build_converter(password=password, enable_ocr=True)
            doc = self._convert(converter, path, password).document
            blocks = self._doc_to_blocks(doc)
            meta = self._extract_docling_meta(doc)
        return blocks, meta

    def _convert_pdf_batches(
        self,
        path: Path,
        password: str | None,
        enable_ocr: bool,
        total_pages: int,
        batch_size: int = _PDF_BATCH_SIZE,
    ) -> tuple[list[Block], dict]:
        """分批转换 PDF 并合并 blocks，返回 (blocks, docling_meta)。"""
        converter = self._build_converter(password=password, enable_ocr=enable_ocr)
        all_blocks: list[Block] = []
        meta: dict = {"title": None, "author": None, "lang": None}
        total_errors = 0

        for start in range(1, total_pages + 1, batch_size):
            end = min(start + batch_size - 1, total_pages)
            result = self._convert(
                converter, path, password, page_range=(start, end)
            )
            all_blocks.extend(self._doc_to_blocks(result.document))
            if start == 1:
                meta = self._extract_docling_meta(result.document)
            errors = getattr(result, "errors", None) or []
            if errors:
                total_errors += len(errors)
                for err in errors[:3]:
                    logger.warning(
                        "PDF p%s 解析错误：%s",
                        getattr(err, "page_no", "?"),
                        getattr(err, "error_message", str(err))[:120],
                    )

        if total_errors:
            logger.warning(
                "PDF 分批解析共 %d 个页面错误（%s）", total_errors, path.name
            )
        return all_blocks, meta

    @staticmethod
    def _pdf_page_count(path: Path) -> int | None:
        """用 pypdfium2 获取 PDF 总页数，失败返回 None。"""
        try:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(path))
            count = len(pdf)
            del pdf
            return count
        except Exception:  # noqa: BLE001
            logger.warning("获取 PDF 页数失败，将一次性转换：%s", path.name, exc_info=True)
            return None

    def _looks_scanned(self, blocks: list[Block]) -> bool:
        """扫描件判定：文本极少（每页平均 < 阈值字符）。"""
        text_chars = sum(len(b.text) for b in blocks if b.type != BlockType.IMAGE)
        pages = max((b.end_page for b in blocks), default=1) or 1
        return text_chars / max(pages, 1) < _SCANNED_PAGE_CHAR_THRESHOLD

    def _doc_to_blocks(self, doc: Any) -> list[Block]:
        """Docling 文档对象 → 归一化 Block 流（阶段一类型映射）。"""
        blocks: list[Block] = []
        try:
            items = doc.iterate_items()
        except AttributeError:
            items = []
        for item, _level in items:
            label = str(getattr(getattr(item, "label", ""), "value", "") or "")
            label = label.lower()
            if label in _SKIP_LABELS:
                continue
            block_type = _LABEL_MAP.get(label, BlockType.PARAGRAPH)
            text = str(getattr(item, "text", "") or "").strip()
            page, bbox = self._extract_prov(item)
            table_data = None
            if block_type == BlockType.TABLE:
                try:
                    markdown = item.export_to_markdown(doc)
                except Exception:  # noqa: BLE001
                    markdown = text
                # 结构化行列数据：优先走 docling DataFrame 导出（TableFormer 识别结果），
                # 失败时退回 TableData 网格手工提取，再退化为空
                headers, rows = self._extract_table_grid(item, doc)
                table_data = {"markdown": markdown, "rows": rows, "headers": headers}
                text = markdown or text
            if not text and block_type != BlockType.IMAGE:
                continue
            blocks.append(
                Block(
                    type=block_type,
                    text=text,
                    level=int(getattr(item, "level", 1) or 1)
                    if block_type == BlockType.HEADING
                    else 0,
                    page=page,
                    bbox=bbox,
                    table_data=table_data,
                    list_level=int(getattr(item, "level", 0) or 0)
                    if block_type == BlockType.LIST_ITEM
                    else 0,
                )
            )
        return blocks

    @staticmethod
    def _extract_table_grid(item: Any, doc: Any) -> tuple[list[str], list[list[str]]]:
        """从 docling TableItem 提取结构化表头与数据行。

        优先 export_to_dataframe（TableFormer 结构化识别结果，自动处理表头/合并单元格）；
        失败时手工遍历 TableData.table_cells 按网格坐标回填。
        """
        # 路径一：DataFrame 导出
        try:
            import pandas as pd

            df = item.export_to_dataframe(doc)
            # 无表头表格 docling 不给列名，columns 退化为 RangeIndex(0,1,2…)，
            # 直接当表头会产出伪表头并"吃掉"全部首行语义，判 RangeIndex 置空
            if isinstance(df.columns, pd.RangeIndex):
                headers: list[str] = []
            else:
                headers = [str(c) for c in df.columns]
            rows = [["" if v is None else str(v) for v in row] for row in df.values.tolist()]
            headers, rows = _clean_table_cells(headers, rows)
            if headers or rows:
                return headers, rows
        except Exception:  # noqa: BLE001
            logger.debug("表格 DataFrame 导出失败，尝试手工网格提取", exc_info=True)
        # 路径二：TableData 网格手工提取
        try:
            data = getattr(item, "data", None)
            cells = getattr(data, "table_cells", None) or []
            num_rows = int(getattr(data, "num_rows", 0) or 0)
            num_cols = int(getattr(data, "num_cols", 0) or 0)
            if not cells or num_rows <= 0 or num_cols <= 0:
                return [], []
            grid = [[""] * num_cols for _ in range(num_rows)]
            header_rows: set[int] = set()
            for cell in cells:
                r = int(getattr(cell, "start_row_offset_idx", 0) or 0)
                c = int(getattr(cell, "start_col_offset_idx", 0) or 0)
                if 0 <= r < num_rows and 0 <= c < num_cols:
                    grid[r][c] = str(getattr(cell, "text", "") or "")
                if getattr(cell, "column_header", False):
                    header_rows.add(r)
            if header_rows:
                hdr_idx = sorted(header_rows)
                headers = grid[hdr_idx[-1]]
                rows = [row for i, row in enumerate(grid) if i not in header_rows]
            else:
                # 无表头标记：首行是数据不是表头，与 docling DataFrame 口径一致
                headers, rows = [], grid
            return _clean_table_cells(headers, rows)
        except Exception:  # noqa: BLE001
            logger.debug("表格网格手工提取失败", exc_info=True)
            return [], []

    @staticmethod
    def _extract_prov(item: Any) -> tuple[int, list[float]]:
        """从 Docling provenance 提取页码与 bbox。"""
        page = 1
        bbox: list[float] = []
        prov = getattr(item, "prov", None) or []
        if prov:
            first = prov[0]
            page = int(getattr(first, "page_no", 1) or 1)
            raw_bbox = getattr(first, "bbox", None)
            if raw_bbox is not None:
                bbox = [
                    float(getattr(raw_bbox, "l", 0.0)),
                    float(getattr(raw_bbox, "t", 0.0)),
                    float(getattr(raw_bbox, "r", 0.0)),
                    float(getattr(raw_bbox, "b", 0.0)),
                ]
        return page, bbox

    @staticmethod
    def _extract_docling_meta(doc: Any) -> dict:
        """从 Docling 文档对象提取元数据 {title, author, lang}。

        Docling 元数据来源：doc.attribs（PDF 内嵌）、doc.DocumentMetadata。
        """
        meta: dict = {"title": None, "author": None, "lang": None}
        try:
            md = getattr(doc, "document_metadata", None) or getattr(doc, "metadata", None)
            if md:
                info = md.to_dict() if hasattr(md, "to_dict") else {}
                meta["title"] = info.get("title") or None
                meta["author"] = info.get("author") or None
                meta["lang"] = info.get("language") or info.get("lang") or None
        except Exception:  # noqa: BLE001
            logger.debug("提取 Docling 元数据失败", exc_info=True)
        # 也尝试从 doc.attribs 提取（部分 Docling 版本埋在这）
        try:
            attrs = getattr(doc, "attribs", None) or {}
            if not meta["title"]:
                meta["title"] = attrs.get("title") or None
            if not meta["author"]:
                meta["author"] = attrs.get("author") or None
            if not meta["lang"]:
                meta["lang"] = attrs.get("language") or None
        except Exception:  # noqa: BLE001
            pass
        return meta
