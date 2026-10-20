"""八阶段切分引擎端到端测试（无模型、无网络）。"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from conftest import (
    FakeLLMClient,
    make_heading,
    make_image,
    make_paragraph,
    make_table,
)

from app.core import structure
from app.core.chunker import Chunker, is_empty_info_chunk
from app.core.tokenizer import count_tokens
from app.core.types import (
    Block,
    BlockType,
    Chunk,
    ChunkingConfig,
    ChunkType,
)


def _sample_blocks() -> list:
    return [
        make_heading("第一章 概述", level=1),
        make_paragraph("这是第一章的第一段内容，用于测试结构聚合的基本行为。"),
        make_paragraph("这是第一章的第二段内容，与上一段属于同一章节。"),
        make_heading("第二章 方法", level=1),
        make_paragraph("第二章描述方法设计。"),
        make_table(),
    ]


class TestChunkPipeline:
    def test_basic_pipeline(self, config):
        # 表格独立成块属 preserve 模式（默认已改 markdown，此处显式指定）
        config.table_handling = "preserve"
        result = Chunker().chunk(_sample_blocks(), config, document_id="doc1")
        all_chunks = result.all_chunks
        assert len(all_chunks) > 0
        # 回填 document_id / token_count
        assert all(c.document_id == "doc1" for c in all_chunks)
        assert all(c.token_count > 0 for c in all_chunks)
        # config_hash 与缓存口径一致
        assert result.config_hash == config.config_hash()
        # 表格独立成块
        table_chunks = [c for c in all_chunks if c.chunk_type == ChunkType.TABLE]
        assert len(table_chunks) == 1
        assert table_chunks[0].table_data is not None

    def test_parent_child_enabled(self, config):
        result = Chunker().chunk(_sample_blocks(), config)
        # 父块不向量化，子块 is_embedded 且指向父块（或独立顶层块）
        assert all(not p.is_embedded for p in result.parents)
        assert all(c.is_embedded for c in result.children)
        parent_ids = {p.id for p in result.parents}
        for child in result.children:
            if child.parent_id is not None:
                assert child.parent_id in parent_ids

    def test_parent_child_disabled(self):
        config = ChunkingConfig(enable_parent_child=False)
        result = Chunker().chunk(_sample_blocks(), config)
        assert result.parents == []
        assert all(c.parent_id is None for c in result.children)
        assert all(c.is_embedded for c in result.children)

    def test_long_paragraph_recursive_split(self, config):
        # 单段落远超 1.5x child_chunk_size（40*1.5=60 token），
        # enable_semantic=False → 走递归兜底
        long_text = "。".join(f"第{i}句内容用于测试递归切分" for i in range(30))
        blocks = [
            make_heading("长文", level=1),
            make_paragraph(long_text),
        ]
        result = Chunker().chunk(blocks, config)
        assert len(result.children) > 1
        assert any(c.strategy == "recursive" for c in result.children)

    def test_overlap_applied_within_section(self, config):
        config.chunk_overlap = 5
        long_text = "。".join(f"句子{i}的内容很长很长" for i in range(20))
        blocks = [make_heading("章节", level=1), make_paragraph(long_text)]
        result = Chunker().chunk(blocks, config)
        text_children = [
            c for c in result.children if c.chunk_type == ChunkType.TEXT
        ]
        if len(text_children) >= 2:
            # 后块开头包含前块末尾内容（overlap）
            tail = text_children[0].content[-6:]
            assert tail[:3] in text_children[1].content

    def test_top_level_chunk_index_keeps_document_order(self, config):
        """顶层块（父块+独生子块）chunk_index 按文档流顺序统一编号。

        回归：父块曾全部排在独生子块之前（3.4/7.2/7.3 提前、目录错位），
        修复后顶层块顺序与解析结果一致。
        """
        blocks = [
            make_heading("甲章", level=1),
            make_paragraph("甲章的第一段，与表格共同构成多子块章节。"),
            make_table(),  # table 独立成块 → 甲章 2 个子块 → 父块
            make_heading("乙章", level=1),
            make_paragraph("乙章只有一个段落，构成独生子块章节。"),
            make_heading("丙章", level=1),
            make_paragraph("丙章的第一段，与表格共同构成多子块章节。"),
            make_table(),  # 丙章同为父块
        ]
        result = Chunker().chunk(blocks, config)
        # 顶层块 = 父块 + 独生子块（parent_id 为空）；落库/预览按 chunk_index 排序
        top = [
            c
            for c in result.all_chunks
            if c.parent_id is None
        ]
        top_sorted = sorted(top, key=lambda c: c.chunk_index)
        crumbs = ["/".join(c.breadcrumb) for c in top_sorted]
        assert crumbs == ["甲章", "乙章", "丙章"], crumbs
        # chunk_index 连续且不重复（与文档流一一对应）
        idxs = [c.chunk_index for c in top_sorted]
        assert idxs == list(range(len(top))), idxs

    def test_small_chunks_merged(self, config):
        config.min_chunk_size = 30  # 小段必然触发合并
        blocks = [
            make_heading("章节", level=1),
            make_paragraph("短段。"),
            make_paragraph("这是一个相对更长的段落，用来和前面的短段落做合并测试。"),
        ]
        result = Chunker().chunk(blocks, config)
        # 短段被合并进相邻 text 块，不单独存在
        short = [c for c in result.children if c.content == "短段。"]
        assert short == []

    def test_llm_review_silent_fallback(self, config):
        config.enable_llm = True

        class BadLLM:
            def chat(self, messages, **kwargs):
                raise ConnectionError("no network")

        # LLM 失败静默回退规则结果
        result = Chunker(llm_client=BadLLM()).chunk(_sample_blocks(), config)
        assert len(result.all_chunks) > 0

    def test_llm_merge(self, config):
        config.enable_llm = True
        llm = FakeLLMClient(
            ['{"should_merge": true, "confidence": 0.9, "summary": "同主题"}']
        )
        blocks = [
            make_heading("章节", level=1),
            make_paragraph("。".join(f"甲句{i}" for i in range(15))),
            make_paragraph("。".join(f"乙句{i}" for i in range(15))),
        ]
        result = Chunker(llm_client=llm).chunk(blocks, config)
        merged = [c for c in result.all_chunks if c.strategy == "llm"]
        # 至少发生一次 LLM 合并，且摘要前缀增强
        if merged:
            assert "【摘要】同主题" in merged[0].content

    def test_llm_merge_conservative_threshold(self, config):
        """保守档（llm_merge_threshold=0.75）：低置信合并被过滤。"""
        config.enable_llm = True
        config.llm_merge_threshold = 0.75
        llm = FakeLLMClient(
            ['{"should_merge": true, "confidence": 0.5, "summary": "弱相关"}']
        )
        blocks = [
            make_heading("章节", level=1),
            make_paragraph("。".join(f"甲句{i}" for i in range(15))),
            make_paragraph("。".join(f"乙句{i}" for i in range(15))),
        ]
        result = Chunker(llm_client=llm).chunk(blocks, config)
        merged = [c for c in result.all_chunks if c.strategy == "llm"]
        assert merged == []  # 0.5 < 0.75，不合并

    def test_llm_merge_threshold_default_keeps_standard(self, config):
        """标准档（threshold=0）：should_merge=true 即合并（现状行为）。"""
        config.enable_llm = True
        config.llm_merge_threshold = 0.0
        llm = FakeLLMClient(
            ['{"should_merge": true, "confidence": 0.4, "summary": "同主题"}']
        )
        blocks = [
            make_heading("章节", level=1),
            make_paragraph("。".join(f"甲句{i}" for i in range(15))),
            make_paragraph("。".join(f"乙句{i}" for i in range(15))),
        ]
        result = Chunker(llm_client=llm).chunk(blocks, config)
        merged = [c for c in result.all_chunks if c.strategy == "llm"]
        assert len(merged) >= 1

    def test_breadcrumb_on_chunks(self, config):
        result = Chunker().chunk(_sample_blocks(), config)
        crumbs = {tuple(c.breadcrumb) for c in result.children}
        assert ("第一章 概述",) in crumbs
        assert ("第二章 方法",) in crumbs

    def test_empty_blocks(self, config):
        result = Chunker().chunk([], config)
        assert result.parents == []
        assert result.children == []


def _mk_chunk(text: str, token_count: int | None = None) -> Chunk:
    """直接构造 text 子块，绕过结构聚合以便单测 overlap/merge。"""
    return Chunk(
        content=text,
        token_count=token_count if token_count is not None else count_tokens(text),
        breadcrumb=["章节"],
    )


class TestOverlapMergeNoDuplication:
    """回归：08-15 质量检查 chunk 重复问题（P0）。

    机制：阶段六 overlap 先于阶段七 merge_small，小块 B 被 prev 尾部
    灌大后又被并回 prev → 同一内容在 chunk 内出现两次；prev 过短时
    tail 复制其全部内容 → 相邻 chunk 整段重叠。
    """

    def test_overlap_skipped_for_short_prev(self, config):
        """prev token < overlap*2 时不加前缀，避免相邻 chunk 整段重复。"""
        config.chunk_overlap = 50
        short_prev = _mk_chunk("短前块内容。" * 8)  # ~40 token < 100
        curr = _mk_chunk("当前块内容。" * 20)
        result = Chunker().apply_overlap([short_prev, curr], config)
        assert result[1].content == "当前块内容。" * 20  # 原样未动

    def test_overlap_prefix_stripped_on_merge(self, config):
        """被合并的小块先剥离 overlap 前缀，合并结果无重复文本。"""
        config.chunk_overlap = 5
        config.min_chunk_size = 30
        prev = _mk_chunk("前块长内容。" * 10)  # 足够长，触发 overlap
        small = _mk_chunk("小块内容句子。" * 3)  # 加前缀后仍 < min，会被合并
        prev_original = prev.content  # merge 会原地修改 prev，先留底
        chunks = Chunker().apply_overlap([prev, small], config)
        assert getattr(chunks[1], "_overlap_prefix", "")  # 确认前缀已加
        merged = Chunker().merge_small(chunks, config)
        assert len(merged) == 1
        # 合并结果 = prev 原文 + 小块原文，overlap 前缀被剥离、无重复
        assert merged[0].content == prev_original + "\n\n" + "小块内容句子。" * 3

    def test_overlap_no_snowball(self, config):
        """tail 基于前块原始内容计算，overlap 不逐块累积传播。"""
        config.chunk_overlap = 10
        config.min_chunk_size = 0  # 关闭合并，隔离观察 overlap 本身
        a = _mk_chunk("甲块内容句子。" * 10)
        b = _mk_chunk("乙块内容句子。" * 12)
        c = _mk_chunk("丙块内容句子。" * 12)
        chunks = Chunker().apply_overlap([a, b, c], config)
        # c 的前缀只应来自 b 的原文尾部，不得包含 a 的内容
        assert "甲块" not in chunks[2].content

    def test_aggregate_dedupes_adjacent_identical_paragraphs(self, config):
        """相邻同文本段落去重（OCR 页眉/页脚重复捕获）。"""
        dup_text = "GB/T 7714-2015 信息与文献 参考文献著录规则"
        blocks = [
            Block(type=BlockType.HEADING, text="章节", level=1),
            Block(type=BlockType.PARAGRAPH, text="正文第一段。"),
            Block(type=BlockType.PARAGRAPH, text=dup_text),
            Block(type=BlockType.PARAGRAPH, text=dup_text),  # OCR 重复捕获
            Block(type=BlockType.PARAGRAPH, text="正文继续。"),
        ]
        section = structure.build_sections(blocks)[0]
        chunks = structure.aggregate_section(section, config)
        content = "\n\n".join(c.content for c in chunks)
        assert content.count(dup_text) == 1

    def test_parent_content_strips_overlap_prefix(self):
        """父块拼接子块时剥离 overlap 前缀，父块中部不重复前块末尾。

        回归：父块 content=子块拼接，子块带阶段六 overlap 前缀时，
        前块末尾文本会在父块中部重复出现（真实数据 chunk[4]/[49] 案例）。
        """
        config = ChunkingConfig(
            enable_parent_child=True,
            enable_semantic=False,
            child_chunk_size=40,
            chunk_overlap=15,
            parent_chunk_size=10000,  # 所有子块归入同一父块
            min_chunk_size=0,
        )
        long_text = "。".join(f"第{i}句内容比较长一些" for i in range(40))
        blocks = [make_heading("章节", level=1), make_paragraph(long_text)]
        result = Chunker().chunk(blocks, config)
        assert result.parents, "应生成父块"
        for parent in result.parents:
            lines = [
                line.strip()
                for line in parent.content.split("\n")
                if len(line.strip()) >= 6
            ]
            dups = {k: v for k, v in Counter(lines).items() if v > 1}
            assert not dups, f"父块含重复行: {list(dups.items())[:2]}"


class TestTruncateNoReplacementChar:
    """回归：overlap 前缀出现 U+FFFD（Planning.txt chunk[14]/[18]/[31]/[40]）。

    机制：tiktoken 对 emoji/生僻字走 byte-fallback 多字节 token，
    truncate_to_tokens 切片落在字节中间时 decode 还原出 U+FFFD，
    随 overlap 前缀灌入后续 chunk 内容。修复为剥离切点侧的 U+FFFD。
    """

    # 含 byte-fallback 字符（emoji + 生僻 CJK），保证存在跨字节切点
    _TEXT = "前面是很长的正常中文内容，" * 6 + "😀🫠𠮷𠮷😀" + "后面继续是很长的中文内容，" * 6

    def test_truncate_never_emits_replacement_char(self):
        from app.core.tokenizer import count_tokens, truncate_to_tokens

        total = count_tokens(self._TEXT)
        for n in range(1, total + 1):
            head = truncate_to_tokens(self._TEXT, n, from_tail=False)
            tail = truncate_to_tokens(self._TEXT, n, from_tail=True)
            assert "\ufffd" not in head, f"head 截断 n={n} 含 U+FFFD: {head!r}"
            assert "\ufffd" not in tail, f"tail 截断 n={n} 含 U+FFFD: {tail!r}"

    def test_overlap_prefix_free_of_replacement_char(self, config):
        config.chunk_overlap = 12
        config.min_chunk_size = 0
        prev = _mk_chunk(self._TEXT)
        curr = _mk_chunk("当前块的正常内容句子。" * 12)
        chunks = Chunker().apply_overlap([prev, curr], config)
        assert "\ufffd" not in chunks[1].content


class TestConfigHashCoverage:
    """config_hash 必须覆盖 ChunkingConfig 的全部字段。

    漏字段的后果：参数改了但缓存 key 不变 → 点「重新切分」命中旧缓存 →
    参数静默不生效（用户视角就是「这个滑块是坏的」）。parent_chunk_size /
    child_chunk_size 曾漏掉，2026-10-08 补齐。
    """

    @staticmethod
    def _alternative(value: object) -> object:
        if isinstance(value, bool):
            return not value
        if isinstance(value, int):
            return value + 1
        if isinstance(value, float):
            return value + 0.01
        return f"{value}-x"

    def test_every_field_affects_config_hash(self):
        base = ChunkingConfig()
        skip = {"CHUNKER_VERSION"}
        for name in ChunkingConfig.__dataclass_fields__:
            if name in skip:
                continue
            alt = self._alternative(getattr(base, name))
            other = replace(base, **{name: alt})
            assert other.config_hash() != base.config_hash(), (
                f"字段 {name} 未参与 config_hash：改它不会让旧切分缓存失效"
            )

    def test_parent_child_sizes_participate(self):
        """回归：父子块大小曾漏出 config_hash，拖滑块重切拿回旧结果。"""
        base = ChunkingConfig()
        assert (
            replace(base, parent_chunk_size=2048).config_hash()
            != base.config_hash()
        )
        assert (
            replace(base, child_chunk_size=512).config_hash()
            != base.config_hash()
        )


class TestDropEmptyBlocks:
    """空信息块过滤开关（2026-09-07 PM 走查：空图片块入库污染检索）。"""

    def _blocks(self) -> list:
        return [
            make_heading("第一章 概述", level=1),
            make_paragraph("这一章先讲背景，内容足够长以便正常成块，不被小块合并吞掉。"),
            make_image(""),  # 无文本图片：聚合后内容为占位符 [图片]
            make_image("图 1 系统架构示意图"),  # 有文本图片：真实信息，必须保留
        ]

    def test_empty_image_dropped_by_default(self, config):
        assert config.drop_empty_blocks is True
        result = Chunker().chunk(self._blocks(), config)
        contents = [c.content for c in [*result.parents, *result.children]]
        assert not any("[图片]" == c.strip() for c in contents), contents
        # 有文本的图片块不受影响
        assert any("系统架构示意图" in c for c in contents)

    def test_empty_image_kept_when_flag_off(self, config):
        config.drop_empty_blocks = False
        result = Chunker().chunk(self._blocks(), config)
        all_chunks = [*result.parents, *result.children]
        images = [c for c in all_chunks if c.chunk_type == ChunkType.IMAGE]
        assert len(images) == 2
        assert any(c.content.strip() == "[图片]" for c in images)

    def test_flag_participates_in_config_hash(self):
        on = ChunkingConfig(drop_empty_blocks=True)
        off = ChunkingConfig(drop_empty_blocks=False)
        assert on.config_hash() != off.config_hash()

    def test_no_ghost_split_by_empty_image(self, config):
        """幽灵打断回归（归因 v3）：「标签 → 空图片 → 表格/文本」流中，空图片
        不再打断文本聚合。修复前：aggregate 遇 image 触发 flush_text，标签孤立
        成 unit，image 随后被 drop_empty_blocks 过滤——打断者消失，残留超短
        标签块。修复后：空图片在 normalize_blocks 丢弃，文本流连续。"""
        blocks = [
            make_heading("二、B类", level=2),
            make_paragraph("（人工智能）"),
            make_image(""),  # 空图片：曾是「幽灵打断者」
            make_paragraph("本节介绍 B 类期刊的遴选标准与代表性刊物。"),
        ]
        result = Chunker().chunk(blocks, config)
        contents = [c.content for c in result.children]
        assert not any(c.strip() == "（人工智能）" for c in contents), contents
        assert any(
            "（人工智能）" in c and "遴选标准" in c for c in contents
        ), contents

    def test_no_ghost_split_off_flag_keeps_flow_unchanged(self):
        """drop_empty_blocks=False 时不做提前丢弃（尊重用户关闭的语义）。"""
        config = ChunkingConfig(drop_empty_blocks=False)
        blocks = [
            make_paragraph("（人工智能）"),
            make_image(""),
            make_paragraph("本节介绍 B 类期刊的遴选标准与代表性刊物。"),
        ]
        result = Chunker().chunk(blocks, config)
        contents = [c.content for c in result.children]
        # 图片仍打断聚合（flag 关闭 = 旧行为）
        assert any(c.strip() == "（人工智能）" for c in contents), contents

    def test_is_empty_info_chunk_contract(self):
        # 文本块由 merge_small 既有逻辑处理，本谓词不重复判定
        assert not is_empty_info_chunk(Chunk(content="", chunk_type=ChunkType.TEXT))
        # 无文本图片（占位符）→ 零信息
        assert is_empty_info_chunk(
            Chunk(content=structure.PLACEHOLDER_IMAGE, chunk_type=ChunkType.IMAGE)
        )
        assert is_empty_info_chunk(Chunk(content="  ", chunk_type=ChunkType.IMAGE))
        # 有文本图片 → 有信息
        assert not is_empty_info_chunk(
            Chunk(content="图 2 流程图", chunk_type=ChunkType.IMAGE)
        )
        # 无 markdown 但仍有结构化行列的表格 → 结构本身是信息
        assert not is_empty_info_chunk(
            Chunk(
                content="",
                chunk_type=ChunkType.TABLE,
                table_data={"headers": ["a"], "rows": [["1"]]},
            )
        )
        # 既无内容也无结构的表格 → 零信息
        assert is_empty_info_chunk(
            Chunk(content="", chunk_type=ChunkType.TABLE, table_data=None)
        )


class TestTinyPieceMerge:
    """阶段四补：过小碎片并入下一碎片（修表格分组标签碎片块）。

    真实病灶（CCF 目录）：「（人工智能）」这类表格分组标签与它引入的表格
    markdown 融成同一 TEXT 单位，超 child_chunk_size 后按段落切分，
    7 token 的标签放不下后面的表格段而被单独吐出。根因在**段落级拆分**
    （碎片继承单位级 block_ids、strategy="structure" 是碎片标记），
    不是 section 边界。
    """

    @staticmethod
    def _table(tag: str, target_tokens: int) -> str:
        """按目标 token 数自适应生成表格 markdown，避免用固定行数去猜分词结果。"""
        header = "| 序号 | 名称 | 链接 |"
        rows: list[str] = []
        text = header
        i = 0
        while count_tokens(text) < target_tokens:
            rows.append(f"| {tag}{i} | 第{i}个会议名称 | dblp{i} |")
            text = header + "\n" + "\n".join(rows)
            i += 1
        return text

    @staticmethod
    def _unit() -> Chunk:
        label = "（人工智能）"
        tables = [TestTinyPieceMerge._table(tag, 45) for tag in ("aaa", "bbb", "ccc")]
        content = "\n\n".join([label, *tables])
        return Chunk(content=content, chunk_type=ChunkType.TEXT)

    def test_merge_tiny_piece_into_next(self):
        # 中间块要够大，否则会继续累积（那是另一个用例的语义）
        pieces = [
            ("小", "structure"),
            ("正文甲内容要足够长一些字数", "structure"),
            ("正文乙", "structure"),
        ]
        merged = Chunker._merge_tiny_pieces(pieces, ChunkingConfig(min_chunk_size=10))
        assert len(merged) == 2
        assert merged[0][0] == "小\n\n正文甲内容要足够长一些字数"  # 过小前缀并入下一碎片
        assert merged[1][0] == "正文乙"

    def test_merge_accumulates_consecutive_tiny_pieces(self):
        pieces = [("a", "structure"), ("b", "structure"), ("正文", "structure")]
        merged = Chunker._merge_tiny_pieces(pieces, ChunkingConfig(min_chunk_size=10))
        assert len(merged) == 1
        assert merged[0][0] == "a\n\nb\n\n正文"

    def test_trailing_tiny_piece_kept(self):
        # 首块够大、末块过小：末尾没有"下一个"可并，保持边界真实，不强行吞掉
        pieces = [
            ("正文甲内容要足够长一些字数", "structure"),
            ("尾", "structure"),
        ]
        merged = Chunker._merge_tiny_pieces(pieces, ChunkingConfig(min_chunk_size=10))
        assert len(merged) == 2
        assert merged[1][0] == "尾"

    def test_label_and_first_table_share_chunk(self, config):
        """端到端：标签与它引入的首个表格同块，且不再产生超短碎片块。"""
        config.child_chunk_size = 40
        config.min_chunk_size = 12
        config.enable_semantic = False
        unit = self._unit()
        unit.token_count = count_tokens(unit.content)

        pieces = Chunker()._split_text_unit(unit, config)
        assert pieces, "应有拆分产物"
        # 先证明本例确实走到了拆分分支（否则下面的断言是空跑）
        assert len(pieces) >= 2, "本例应产生多于一个碎片，否则未覆盖拆分路径"
        tiny = [p for p in pieces if p.token_count < config.min_chunk_size]
        assert tiny == [], f"仍存在超短碎片：{[(p.content[:20], p.token_count) for p in tiny]}"
        assert any("（人工智能）" in p.content and "aaa0" in p.content for p in pieces)
