# Tessera 架构设计文档

> 本地 RAG 文档摄入工具 —— 多格式解析 → 智能切分 → 向量化 → 存储 → 检索验证

---

## 一、产品定位

Tessera（镶嵌画中的小块）是一个本地运行的 RAG 文档摄入工具。核心价值：将多格式文档解析为结构化内容，通过智能切分策略产出高质量 chunk，向量化后存入向量数据库，并提供可视化的切分预览与检索验证能力。

核心差异化：
- **切分可视化**：先看切分效果再入库，不满意可调参重切
- **混合切分策略**：结构感知 + 语义切分 + LLM 辅助 + 递归兜底
- **Parent-Child 双层检索**：子块精准匹配，父块提供完整上下文
- **零配置启动**：默认本地运行，无需外部服务

---

## 二、技术栈

### 前端
- React 18 + TypeScript + Vite
- Tailwind CSS + shadcn/ui
- React Router v7
- Zustand（客户端状态）+ TanStack Query（服务端状态）
- 原生 fetch + WebSocket

### 后端
- Python 3.10+ + FastAPI
- SQLAlchemy 2.0（异步）+ Pydantic v2
- SQLite（元数据存储）
- asyncio + ProcessPoolExecutor（仅 Docling 解析走进程池；切分/向量化/检索在主进程共享模型单例）

### AI / ML
- 文档解析：Docling（MIT，本地，多格式统一为文档树）
- 向量化：BGE-M3 本地默认（FlagEmbedding），Qwen3-Embedding 0.6B/4B 可选，支持 OpenAI 兼容 API
- 精排：bge-reranker-v2-m3（RRF 融合后 top20 重排 + min_score 阈值）
- LLM 辅助：DeepSeek V4 Flash 默认，支持任意 OpenAI 兼容端点
- 向量库：LanceDB（默认）/ sqlite-vec（兜底，LanceDB 初始化失败自动降级）/ qdrant / pgvector（后两档可切换、未做真实联调）

### 项目结构

```
tessera/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app + lifespan
│   │   ├── config.py            # 环境变量配置
│   │   ├── database.py          # SQLAlchemy engine/session
│   │   ├── api/                 # 路由层
│   │   │   ├── documents.py
│   │   │   ├── chunks.py
│   │   │   ├── search.py
│   │   │   ├── evaluation.py    # 评测（测试集/实验）
│   │   │   ├── export.py        # JSONL 导出
│   │   │   ├── parse_preview.py # 解析预览/清洗
│   │   │   ├── settings.py
│   │   │   └── ws.py            # WebSocket
│   │   ├── core/                # 业务逻辑层
│   │   │   ├── parser.py        # Docling 封装（PDF 分批转换）
│   │   │   ├── chunker.py       # 主切分引擎（八阶段）
│   │   │   ├── structure.py     # 归一化/面包屑/结构聚合
│   │   │   ├── semantic.py      # 语义切分（embedding 断崖）
│   │   │   ├── recursive.py     # 递归字符切分（兜底）
│   │   │   ├── llm_chunker.py   # LLM 辅助切分
│   │   │   ├── parent_child.py  # 父子关系构建
│   │   │   ├── tokenizer.py     # tiktoken 计数/切片
│   │   │   ├── quality.py       # 质量检测（页眉重复/乱码等）
│   │   │   ├── cleaner.py       # LLM 清洗（off/problem/full）
│   │   │   ├── evaluator.py     # 评测引擎（hit_rank/Recall/MRR）
│   │   │   ├── exporter.py      # JSONL 导出 + 思维链清洗
│   │   │   ├── metadata.py      # 元数据提取（title/author/lang）
│   │   │   ├── embedder.py      # 向量化（BGE-M3/Qwen3/API）
│   │   │   ├── reranker.py      # 精排（bge-reranker-v2-m3）
│   │   │   ├── vector_store.py  # 向量库抽象 + 工厂（降级）
│   │   │   ├── stores/          # 四档实现（lancedb/sqlite_vec/qdrant/pgvector）
│   │   │   └── retriever.py     # 混合检索（RRF + Rerank）
│   │   ├── models/
│   │   │   ├── db.py            # SQLAlchemy ORM
│   │   │   └── schemas.py       # Pydantic schemas
│   │   ├── services/
│   │   │   ├── runtime.py       # 运行时单例容器（懒加载+预热状态）
│   │   │   ├── model_manager.py # 模型按需下载
│   │   │   ├── cache.py         # 解析/切分/embedding 缓存
│   │   │   ├── settings_store.py# 设置持久化（deep_merge）
│   │   │   ├── llm.py           # LLM 客户端
│   │   │   └── file_storage.py  # 文件管理
│   │   └── workers/
│   │       ├── tasks.py         # 后台任务
│   │       └── pool.py          # Docling 解析进程池
│   ├── data/                    # 运行时数据（gitignore，路径可配置）
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   │   └── ui/              # shadcn/ui
│   │   ├── hooks/
│   │   ├── lib/
│   │   └── types/
│   └── package.json
├── docker-compose.yml
└── README.md
```

分层原则：api 层只做 HTTP 参数校验和响应格式化；core 层纯业务逻辑不依赖 FastAPI；services 层封装外部调用；models 层定义数据结构。

---

## 三、系统架构图

```
┌─────────────────────────────────────────────────────────────┐
│                      Tessera Web UI                         │
│  [文档管理] [切分预览] [检索测试] [模型设置] [切分设置]       │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP / WebSocket
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI Backend                         │
│                                                             │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐ │
│  │  Upload  │──▶│  Parse   │──▶│  Chunk   │──▶│ Preview  │ │
│  │ Service  │   │  Engine  │   │  Engine  │   │  Engine  │ │
│  └──────────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘ │
│                 Docling        ┌───┴───┐         用户确认   │
│                 本地解析        │结构切分│           │        │
│                               │语义切分│           ▼        │
│                               │LLM 辅助│   ┌──────────┐    │
│                               │递归兜底│   │  Embed   │    │
│                               └───┬───┘   │  Engine  │    │
│                                   │       └────┬─────┘    │
│                                   │      BGE-M3/API      │
│                                   │            │          │
│                                   │            ▼          │
│                                   │       ┌──────────┐    │
│                                   │       │  Store   │    │
│                                   │       │  Engine  │    │
│                                   │       └────┬─────┘    │
│                                   │ LanceDB/sqlite-vec/Qdrant/PG│
│                                   ▼                       │
│                             ┌──────────┐                  │
│                             │ Retrieve │◀── 搜索测试      │
│                             └──────────┘                  │
└───────────────────────────────────────────────────────────┘
         │              │              │
         ▼              ▼              ▼
    ┌─────────┐   ┌─────────┐   ┌─────────────┐
    │ 用户选  │   │ SQLite  │   │  向量存储    │
    │ 择目录  │   │ 元数据  │   │  向量索引    │
    └─────────┘   └─────────┘   └─────────────┘
```

---

## 四、核心交互流程

1. 用户在「文档管理」页选择文件，支持多选（也支持 URL 导入）。可选择保存目录（默认数据目录）
2. 后端检查文件类型：不支持的格式标红提示；混合类型弹警告但不阻止
3. 文件存盘，Docling 解析为结构化文档树（WebSocket 推进度；PDF 分批转换防内存耗尽），状态停在 `parsed` 待验收
4. 「解析预览」页验收解析结果：章节树/表格/图片块检查，可跑质量报告与 LLM 清洗（off/problem/full 三档，产出 cleaned 变体缓存）
5. 切分引擎按策略切分，结果存 SQLite，状态为 `chunked`（待确认）
6. 前端跳转「切分预览」页：左侧原文可滚动，右侧 chunk 卡片列表
   - 每个 chunk 显示：类型标签、章节路径、token 数
   - 顶部可调节切分参数，点「重新切分」刷新
   - 用户可勾选/取消勾选特定章节
7. 用户确认后点「入库」→ 向量化 → 写入向量库
8. 「检索测试」页输入 query（可按文档/类型过滤），查看召回 chunks（含稠密/稀疏/精排分）
9. 「质量评测」页导入 QA 测试集跑实验（Recall@k / MRR），「导出」页出 JSONL 训练数据

**关键设计：解析验收、切分、入库三段解耦。** 每段先看效果再推进，不满意调参重来。

---

## 五、文档处理状态机

```
uploaded → parsing → parsed → chunking → chunked → embedding → indexed
                          ↘ 失败 → parse_failed / chunk_failed / embed_failed / store_failed
```

| 状态 | 含义 |
|------|------|
| uploaded | 文件存盘，DB 记录创建 |
| parsing | Docling 解析中，WebSocket 推进度 |
| parsed | 解析完成，文档树缓存，待解析验收 |
| chunking | 切分中 |
| chunked | 切分完成，chunks 存 DB，可预览 |
| embedding | 向量化 + 写入向量库中 |
| indexed | 全部完成，可检索 |
| encrypted | 加密 PDF，等待用户输入密码 |
| parse_failed | 解析失败 |
| chunk_failed | 切分失败 |
| embed_failed | 向量化失败 |
| store_failed | 向量库写入失败（可重试 upsert） |

用户调参重切时，状态从 `chunked`/`indexed` 回退到 `chunking`。
解析产物有 raw / cleaned 两个变体（`Document.parse_variant`，LLM 清洗产出 cleaned）。

---

## 六、核心数据模型

### Document

| 字段 | 类型 | 说明 |
|------|------|------|
| id | str (UUID) | 主键 |
| filename | str | 原始文件名 |
| file_path | str | 存储路径（绝对路径或相对数据目录） |
| file_hash | str | SHA256，缓存判断 |
| file_type | str | pdf / docx / pptx / md / txt / html |
| file_size | int | 字节数 |
| page_count | int | 页数 |
| status | str | 状态机值 |
| parse_variant | str | 解析变体：raw / cleaned（LLM 清洗产物） |
| title | str | 文档标题（默认文件名去扩展名） |
| author | str | 作者，可空 |
| lang | str | 检测语言 zh/en，可空 |
| parse_cache_path | str | Docling 解析结果缓存路径 |
| chunk_count | int | chunk 总数 |
| embedded_count | int | 已向量化数 |
| error_message | str | 失败原因 |
| created_at | datetime | |
| updated_at | datetime | |

索引：file_hash、status、created_at。

### Chunk

| 字段 | 类型 | 说明 |
|------|------|------|
| id | str (UUID) | 主键 |
| document_id | str (UUID) | 所属文档，级联删除 |
| parent_id | str (UUID) | 父 chunk ID，子 chunk 填 |
| chunk_index | int | 顺序号，从 0 开始 |
| content | text | chunk 文本 |
| chunk_type | str | text / table / code / image / heading |
| breadcrumb | JSON | 章节路径数组，如 ["第3章","3.2 实验"] |
| page_number | int | 起始页码 |
| end_page | int | 结束页码 |
| token_count | int | token 数 |
| char_count | int | 字符数 |
| strategy | str | structure / semantic / llm / recursive |
| bbox | JSON | 页面边界 [x1,y1,x2,y2] |
| block_ids | JSON | 包含的 Block ID 列表，用于原文定位高亮 |
| table_data | JSON | 表格专用 {markdown, rows, headers} |
| heading_text | str | 所属章节标题文本 |
| embedding_model | str | 向量化模型标识 |
| is_embedded | bool | 是否已入向量库 |
| vector_id | str | 向量库中的 ID |
| created_at | datetime | |

索引：document_id、(document_id, chunk_index)、parent_id、is_embedded。

### Settings（单行配置表）

**AppConfig**：
```json
{
  "data_dir": "D:/TesseraData",
  "max_file_size_mb": 500,
  "cache_size_limit_gb": 2
}
```

**LLMProvider**（可配多个，一个 default）：
```json
{
  "id": "uuid",
  "name": "DeepSeek V4 Flash",
  "provider_type": "deepseek",
  "base_url": "https://api.deepseek.com",
  "api_key": "sk-...",
  "model_name": "deepseek-v4-flash",
  "display_name": "DeepSeek V4 Flash",
  "is_default": true,
  "enabled": true
}
```
provider_type 为 `custom` 时用户填 base_url，api_format 固定为 OpenAI 兼容。

**EmbeddingConfig**：
```json
{
  "provider_type": "local",
  "model_name": "BAAI/bge-m3",
  "api_key": null,
  "base_url": null,
  "dimensions": 1024
}
```

**RerankerConfig**：
```json
{
  "enabled": false,
  "model_name": "BAAI/bge-reranker-v2-m3",
  "top_n": 20,
  "min_score": 0.1
}
```
min_score 为精排最低分阈值（0 = 不过滤），0.1 校准于 bge-reranker-v2-m3 的 sigmoid 双峰（正例 ~0.99 / 负例 ~0.01）；换模型需重新校准。

**VectorStoreConfig**：
```json
{
  "backend": "lancedb",
  "connection_url": null,
  "collection_name": "tessera",
  "config": {}
}
```
backend 可选 lancedb（默认）/ sqlite_vec / qdrant / pgvector。LanceDB 初始化失败自动降级 sqlite-vec 并标记 `degraded_from`。

**DownloadConfig**：
```json
{ "source": "hf_mirror" }
```
source 可选 huggingface / hf_mirror（国内推荐）/ modelscope。

**ChunkingConfig**：
```json
{
  "chunk_size": 512,
  "chunk_overlap": 50,
  "parent_chunk_size": 1024,
  "child_chunk_size": 256,
  "enable_parent_child": true,
  "enable_semantic": true,
  "enable_llm": false,
  "llm_merge_threshold": 0,
  "semantic_threshold": 0.3,
  "table_handling": "markdown",
  "min_chunk_size": 50,
  "drop_empty_blocks": true
}
```
llm_merge_threshold：LLM 合并置信度阈值（0 = 标准档，should_merge 即合并）。drop_empty_blocks：零信息块（空图片/无结构表格）不入库。

### Task

| 字段 | 类型 | 说明 |
|------|------|------|
| id | str (UUID) | 主键 |
| document_id | str (UUID) | 关联文档 |
| task_type | str | parse / chunk / embed / delete |
| status | str | pending / running / completed / failed |
| progress | int | 0-100 |
| message | str | 状态描述 |
| error | str | 失败原因 |
| created_at | datetime | |
| started_at | datetime | 可空 |
| completed_at | datetime | 可空 |

---

## 七、切分引擎核心算法

### 输入
Docling 解析后的结构化元素流：标题（带 level）、段落、表格、图片、列表项、公式，每个元素带页码、bbox、文本内容、section 归属。

引擎带版本号 `ChunkingConfig.CHUNKER_VERSION`（当前 `empty-img-early-1`），混入切分缓存
config_hash——切分逻辑变更时递增版本号即可让旧切分缓存整体失效，避免"修了但用户拿回旧结果"。

### 阶段一：元素归一化

将 Docling 内部类型映射为统一 Block：

```
Block:
  id          唯一标识
  type        heading | paragraph | table | code | image | list_item
  text        文本内容
  level       标题层级 1-6（仅 heading）
  page        起始页码
  bbox        [x1,y1,x2,y2]
  table_data  表格专用 {markdown, rows, headers}
  list_level  列表嵌套层级
```

清洗规则：过滤页眉页脚（Docling 标记为 header/footer）、过滤纯空白、合并被拆散的连续文本。
**空图片块（text 空或占位符）在此丢弃**（随 drop_empty_blocks 开关）——必须在聚合之前，
否则聚合层遇 image 打断文本流（flush_text）而空图片随后又被零信息过滤丢掉，"打断者消失、
残留孤立小段"（幽灵打断，详见 PAIN_POINTS.md）。

### 阶段二：面包屑追踪

维护标题栈。遇到 heading 弹出栈中 level >= 当前 level 的标题，当前入栈。每个非 heading 元素记录栈快照作为 breadcrumb。

### 阶段三：结构聚合（主策略）

按 section（同一标题下）聚合 Block：
- section 总 token <= parent_chunk_size → 整个 section 作为父 chunk
- section 超限 → 标记待拆分
- table Block 独立成 chunk（不与其他类型合并），保留完整 table_data
- code Block 独立成 chunk
- image Block 独立成 chunk，content 取 caption 或 `[图片]` 占位
- 连续 list_item 合并为一个 chunk

### 阶段四：长 section 拆分

**4a 段落级拆分**：按 paragraph 边界累加，超 child_chunk_size 时断开。单个段落超 child_chunk_size x 1.5 进入 4b。

**4b 语义拆分**：
- 按句子切分（中文 `。！？；`，英文 `.!?;`）
- BGE-M3 批量编码所有句子
- 计算相邻句子余弦相似度 sim[i] = cos(sent[i], sent[i+1])
- 计算相似度均值 μ 和标准差 σ
- 断崖点：sim[i] < max(μ - σ, semantic_threshold)
- 在断崖处断开，组内仍超限则找次优断崖
- 无明显断崖 → 进入 4c

**4c 递归字符拆分（兜底）**：按分隔符优先级递归：
双换行 → 单换行 → `。！？；.!?;` → `，、，` → 空格 → 字符级

**4d 过小碎片合并**：段落级拆分甩出的碎片（token < min_chunk_size）作下一碎片的前缀合并
（`_merge_tiny_pieces`）——典型病灶是表格分组标签（如「（人工智能）」）被表格段挤出成
独立小块。碎片的判据是继承了单位级完整 `block_ids`（拆分碎片标记 strategy="structure"）。

**阶段四补：空信息块过滤**（drop_empty_blocks 开关，默认开）：非文本块但内容为空或只剩
占位符（`[图片]`）的 chunk 不入库；带结构化行列的表格（headers/rows）保留——结构本身
是信息。位置在 overlap/merge/父子块之前，零信息块不参与任何后续产物。

### 阶段五：LLM 辅助（可选）

触发条件：enable_llm=true 且配置了 LLM provider。

- 对相邻 chunk 边界，取前 chunk 末尾 200 字 + 后 chunk 开头 200 字
- LLM 判断是否应合并（同一主题应合并，话题转变保持分开）
- 返回 JSON：`{"should_merge": bool, "confidence": float, "summary": "..."}`
- should_merge=true 且合并不超限则合并
- summary 追加到 chunk content 前增强检索
- 对无标题结构/OCR 质量差的文档，LLM 可建议章节边界
- LLM 失败时静默回退到规则结果

### 阶段六：Overlap

- 相邻子 chunk 添加 overlap（默认 50 token）
- 前 chunk 末尾 overlap_size token 追加到后 chunk 开头
- **对称 skip**：prev 或 curr 原文 token < overlap*2 时不加前缀（小块被 overlap 灌大后
  会逃过 min_chunk_size 合并，导致相邻整段重叠）
- tail（section 尾块）基于 prev **原始内容**计算 overlap，且从 prev 自身剥 overlap 前缀
  （防前缀滚雪球累积）；父块拼接子块时同样剥离子块的 overlap 前缀（防中部重复）
- tiktoken 切 overlap 尾部时若切点落在 byte-fallback 多字节 token 中间，decode 后剥
  U+FFFD 替换字符（防内容污染）
- 不跨 section，table/code 不参与

### 阶段七：小块合并与清洗

- token < min_chunk_size 的 chunk 尝试与同 section 相邻 chunk 合并
- 合并前剥离 overlap 前缀；相邻同文本段落去重（OCR 页眉/页脚重复捕获）
- table/code/image 不参与合并
- 去首尾空白、去重复换行、丢弃空 chunk

### 阶段八：Parent-Child 构建

- 父 chunk：section 级，token ≈ parent_chunk_size，is_embedded=false
- 子 chunk：细粒度，token ≈ child_chunk_size，is_embedded=true，parent_id 指向父 chunk
- 子 chunk 向量化入向量库，父 chunk 存 SQLite 不向量化
- 检索：向量库命中子 chunk → 取 parent_id → 取父 chunk 完整 content 返回
- enable_parent_child=false 时全部直接向量化，无父子关系

### 算法伪代码

```python
def chunk(document_tree, config):
    blocks = normalize(document_tree)
    blocks = attach_breadcrumbs(blocks)
    parent_chunks = structure_aggregate(blocks, config)

    all_children = []
    for parent in parent_chunks:
        if parent.token_count > config.parent_chunk_size:
            children = split_long_section(parent, config)
            # 内部：段落拆分 → 语义拆分 → 递归兜底 → LLM检查 → overlap
        else:
            children = [parent]
        children = merge_small_chunks(children, config)
        children = assign_parent(children, parent)
        all_children.extend(children)

    if config.enable_llm:
        all_children = llm_review(all_children, config)

    return annotate_and_index(parent_chunks, all_children)
```

---

## 八、向量化设计

### Embedding 模型

默认 BGE-M3（BAAI/bge-m3）：1024 维，同时输出稠密向量 + 稀疏词法权重 + ColBERT 多向量，中英文强，8192 token 输入，本地运行，Apache 2.0。

支持切换到任意 OpenAI 兼容的 Embedding API（OpenAI text-embedding-3-small/large、通义、智谱、自部署服务）。在设置页配置 provider_type=openai_compatible、base_url、model_id、api_key。

**约束**：不同 embedding 模型向量空间不同，不可混用。切换模型后已有向量必须全部重建，设置页弹确认提示。API embedding 只返回稠密向量，稀疏检索由 FTS 文本通道承担。

### 向量化流程

用户点「确认入库」后：
1. 从 SQLite 查出该文档所有 is_embedded=false 的子 chunk
2. 批量取 content，tokenizer 检查长度（chunk_size=256 远小于 8192，不截断）
3. 批量编码（batch_size 默认 32，GPU 自动 fp16 加载，CPU fp32）
4. 稠密向量批量写入向量库；稀疏侧统一走 FTS 文本通道（LanceDB `content_fts` 影子列 /
   sqlite-vec FTS5 影子表），Qdrant 走 lexical_weights、pgvector 走 tsvector
5. 更新 Chunk 表：is_embedded=true、vector_id、embedding_model
6. 父 chunk 不向量化，仅存 SQLite
7. WebSocket 推进度

### Embedder 抽象

```
Embedder（抽象基类）
  ├── LocalEmbedder      # BGE-M3/Qwen3-Embedding，懒加载单例（加载互斥锁）
  └── APIEmbedder        # OpenAI 兼容 /embeddings 接口，批量调用
```

统一接口 `embed(texts: list[str]) -> EmbeddingResult`，返回稠密向量（+ 稀疏权重如果有）。
模型懒加载（首次使用才载入），但启动后会**后台预热**（lifespan 拉起 `_warmup_models`，
仅预热已下载模型），让首次检索不再等待 40s+ 的冷加载；`/api/health` 返回
`models: {embedder_ready, reranker_ready}` 供观测。

---

## 九、检索设计

### 混合检索流程

用户输入 query：
1. **Query 向量化**：同一 embedder 将 query 编码为稠密向量（+ 稀疏权重）
2. **稠密检索**：向量库 ANN 搜索，取 top_k x 2 候选，返回 chunk_id + 稠密分数
3. **稀疏检索**：对 chunk content 的 FTS 索引做 BM25 全文检索（LanceDB Tantivy 影子列 /
   sqlite-vec FTS5 影子表；Qdrant 走原生稀疏、pgvector 走 tsvector）。分词为 CJK 相邻二元
   （`FTS_TOKENIZER_VERSION=2`，写入/查询共用 `fts_tokenize`）——逐字分词 + OR 会让任何
   含常用字的长文本命中，叠加 BM25 长度归一化会把正确答案系统性挤出（见 PAIN_POINTS.md）
4. **RRF 融合**：Reciprocal Rank Fusion 合并两路结果，k=60
5. **Rerank（可选）**：bge-reranker-v2-m3 对融合后 top20 候选精排；`min_score` 阈值
   （默认 0.1）滤掉低相关候选，被滤数记入 `threshold_dropped`（空态语义区分用）
6. **父 chunk 展开**：命中子 chunk → parent_id → SQLite 取父 chunk 完整 content
7. **返回**：父 chunk 全文、子 chunk 匹配片段、来源文件、页码、面包屑、融合分数、
   两路排名与两路原始分（dense_score / sparse_score，供通道诊断）、rerank_score

### RRF 公式

```
score(d) = Σ 1/(k + rank_i(d))    k=60
```

稠密和稀疏两路各算排名，求和。两路都命中的 chunk 分数叠加。

### 各向量库的混合检索实现

| 后端 | 稠密检索 | 稀疏检索 | 融合位置 |
|------|---------|---------|---------|
| LanceDB（默认） | LanceDB ANN | Tantivy FTS（`content_fts` 影子列） | 应用层 RRF |
| sqlite-vec（兜底） | vec0 KNN（不可用时暴力余弦） | FTS5 BM25（影子表） | 应用层 RRF |
| Qdrant | Qdrant 稠密向量 | Qdrant 稀疏向量原生 | 应用层 RRF |
| pgvector | pgvector HNSW | PostgreSQL tsvector 全文 | 应用层 RRF |

（RRF 与 Rerank 统一在应用层执行，各后端只负责返回 dense / sparse 两路候选。）

### 过滤

支持按文档过滤（document_ids，只在选中文档内搜）和按类型过滤（chunk_types：text/table/
code/image/heading），向量库 metadata 过滤实现（组间 AND、组内 IN）；前端检索页有筛选入口。

### 检索返回结构

```json
{
  "query": "实验用的什么数据集",
  "used_rerank": true,
  "rerank_skipped": null,
  "results": [
    {
      "chunk_id": "uuid",
      "parent_content": "3.2 实验设计\n本节实验使用...完整父 chunk...",
      "matched_child_text": "实验采用 MNIST 和 CIFAR-10...",
      "source_file": "论文.pdf",
      "page_number": 7,
      "breadcrumb": ["第3章", "3.2 实验设计"],
      "chunk_type": "text",
      "score": 0.032,
      "dense_rank": 1,
      "sparse_rank": 3,
      "dense_score": 0.87,
      "sparse_score": 1.63,
      "rerank_score": 0.989
    }
  ],
  "total": 5,
  "latency_ms": 230,
  "threshold_dropped": 0
}
```

---

## 十、存储方案

| 档位 | 引擎 | 适用规模 | 部署方式 | 特点 |
|------|------|---------|---------|------|
| 小（默认） | LanceDB | 几千~几万 chunk | pip install 嵌入式 | 本地文件存储，Tantivy FTS 影子列，零配置；初始化失败自动降级 sqlite-vec |
| 兜底 | sqlite-vec | <5000 chunk | 零服务，单 .db 文件 | vec0 KNN + FTS5 影子表共存一库，sqlite-vec 不可用时退化暴力余弦 |
| 中 | Qdrant | 几十万~百万 chunk | Docker 单容器/本地二进制 | Rust 核心，原生稀疏向量 + 元数据过滤（未做真实联调，设置页有警示） |
| 大 | pgvector | 百万+ | 复用现有 PostgreSQL | SQL 复杂查询，事务保证（未做真实联调，设置页有警示） |

架构：`VectorStore` 抽象基类，四个实现类（`core/stores/`）。设置页选择后端并填连接信息，运行时动态加载。LanceDB 初始化失败时自动降级到 sqlite-vec 并提示用户。

**数据存储目录**：用户可在设置中配置 data_dir，默认 `~/Tessera/data`。所有运行时数据（上传文件、解析缓存、SQLite、向量库文件、缓存）都存于此目录。上传文件时也可单独选择保存位置。

---

## 十一、缓存策略

三级缓存，根目录为 `{data_dir}/cache/`：

**解析缓存**：
- key: `parse_{file_hash}.json`（LLM 清洗产物另存 `parse_{file_hash}_cleaned.json`）
- value: Docling 文档树序列化 JSON
- 命中条件：文件 hash 不变直接复用，跳过 Docling 解析；标题层级推断（infer_headings）
  在读缓存时惰性执行，解析逻辑修复对旧缓存自动生效
- 失效：文件重新上传/hash 变了自动不命中；重解析时清解析+切分缓存（clear_document_caches）
- **注意**：缓存文件 mtime 不等于解析时间（LRU 读取会 os.utime 触碰），判断新旧看内容特征

**切分缓存**：
- key: `chunk_{file_hash}_{variant}_{config_hash}.json`（variant = raw / cleaned）
- config_hash: chunk_size + chunk_overlap + enable_semantic + enable_llm +
  llm_merge_threshold + enable_parent_child + parent_chunk_size + child_chunk_size +
  table_handling + semantic_threshold + min_chunk_size + drop_empty_blocks +
  CHUNKER_VERSION 序列化后取 hash（字段有覆盖率测试守卫，加参数必须进 hash）
- value: chunk 列表 JSON
- 命中条件：文件没变且切分参数没变，直接返回缓存
- 失效：改任何切分参数 → config_hash 变 → 不命中，但解析缓存仍命中（只重切不重解析）；
  清洗参数变更只清 cleaned 变体（cache.clear_chunk(file_hash, variant)）；LLM 开启时不缓存

**Embedding 缓存**：
- key: `embed_{text_hash}_{model_name}_{_EMBED_CACHE_VERSION}.npy`
- value: 单个文本的向量
- 命中条件：同文本同模型同版本直接取缓存向量
- 价值：重新入库只 embed 新增/变更 chunk；切换向量库后端复用已有向量
- 失效：换 embedding 模型 → model_name 变；模型实现变更 → 递增 _EMBED_CACHE_VERSION；
  删除文档时按内容哈希 clear_embed（跨文档共享缓存的同文本缓存一并失效，代价=重算一次）

**缓存管理**：
- 设置页显示缓存占用大小，提供「清理缓存」按钮
- 可按类型清理（解析/切分/embedding/全部）
- 自动 LRU：缓存总大小超配置上限（默认 2GB）时按最后访问时间淘汰
- 删除文档时级联清理该文档的切分缓存和 embedding 缓存（解析缓存保留，同 hash 重新上传可复用）

---

## 十二、前端交互设计

### 切分预览页（核心页面）

布局：顶部工具栏 + 左右分栏。

**顶部工具栏**：
- 文档选择器（下拉切换已切分的文档）
- 参数面板：chunk size 滑块、overlap 滑块、策略开关（语义/LLM/Parent-Child）、表格处理下拉
- 「重新切分」按钮（参数改了不自动重切，点按钮才触发）
- 「确认入库」主按钮
- 切分时显示进度条和骨架屏

**左侧原文面板**（50% 宽）：
- Docling 解析结果渲染为带格式 HTML：标题 h1-h6、表格 table、代码 pre/code、图片缩略图
- 每个 Block 元素标 `data-block-id`
- 滚动时顶部面包屑跟随更新
- 可切换「解析文本视图」和「PDF 原文视图」（PDF.js 渲染）

**右侧 chunk 面板**（50% 宽）：
- chunk 卡片纵向列表
- 卡片顶部：类型色标（正文蓝/表格绿/代码橙/图片紫）、章节路径、token 数标签
- 卡片中部：chunk 内容（表格渲染为表格，代码等宽字体）
- 卡片底部：页码、策略来源标签、父 chunk 标识（子 chunk 显示可点击展开的父块引用）
- 卡片可点击选中

**左右联动**：
- 鼠标移入右侧 chunk 卡片 → 左侧该 chunk 对应文本区域高亮（黄色半透明背景），不在视口则左侧自动滚动
- 滚动左侧原文 → Intersection Observer 检测可见 Block → 找到对应 chunk → 右侧自动高亮并滚动到视口
- 点击右侧 chunk 卡片 → 左侧平滑滚动到该 chunk 起始位置，文本高亮闪烁

**章节树**（左侧面板顶部可折叠）：
- 显示文档标题层级树
- 点击跳转对应位置
- 勾选/取消勾选控制哪些章节入库

### 其他页面交互要点

**文档管理页**：拖拽上传区域、文件列表带状态标签（彩色圆点）、多选后批量操作工具栏、悬停显示操作按钮。

**检索测试页**：输入框回车搜索，结果列表带高亮关键词、相似度分数进度条、点击展开完整上下文、搜索历史下拉。

**设置页**：LLM 配置弹窗按参考图实现（选择页 → DeepSeek/自定义表单 → 连通性测试），分 Tab 切换 LLM/Embedding/向量库/切分参数/存储路径。

---

## 十三、错误处理与边界情况

| 场景 | 处理方式 |
|------|---------|
| 加密 PDF | 检测到加密，状态置 `encrypted`，前端弹密码输入框，后端解密后重新解析，密码不存盘，用户取消则跳过 |
| 不支持格式 | 前端 accept 过滤 + 后端 MIME 校验，直接拒绝并提示支持的格式列表 |
| 损坏文件/解析失败 | try-catch 包裹，状态 `parse_failed`，error_message 存异常信息，列表标红显示原因，提供重试按钮，不阻塞其他文件 |
| 超大文件 | 上传前检查大小，>100MB 弹警告，>max_file_size_mb（默认 500）拒绝；Docling 分页处理不全量加载 |
| 扫描件/OCR 质量差 | 解析后文本极少（每页<50 字）判定为扫描件，自动启用 OCR，前端提示 |
| LLM/API 调用失败 | 重试 1 次（指数退避），仍失败则该 chunk 回退规则切分，不阻断流程，chunk 标「LLM 辅助失败」 |
| API key 无效 | 设置页连通性测试阶段报错，不等到切分时才发现 |
| 磁盘空间不足 | 上传前检查剩余空间（需 > 文件大小 x 3），向量化前也检查，不足则提示 |
| 空文件/无内容 | 解析后文档树为空，状态 `parse_failed`，提示「未检测到文本内容」 |
| 重复文件 | 上传时算 file_hash，已存在同 hash 文档则提示「该文件已存在，是否重新上传？」 |
| 进程崩溃/中断 | Task 表持久化任务状态，重启扫描 pending/running 任务标记 failed 并允许重试，已完成结果不丢 |
| 向量库连接失败 | 启动时检测，LanceDB 失败自动降级 sqlite-vec（标 degraded_from）；Qdrant/pgvector 连接失败提示用户检查配置 |
| 中文路径/文件名 | 全链路使用 pathlib 和 UTF-8，OpenCV 等不支持中文路径的库用 numpy 读取替代 |

---

## 十四、LLM 配置 UI 设计

参考用户提供的设计图，设置页「模型设置」中添加 LLM 时：

**选择页**：两个选项卡片——「自定义模型」（立方体图标）和「DeepSeek」（DeepSeek logo），右侧箭头可点击。

**选择 DeepSeek 时**（标题「通过服务商添加」）：
- 服务商（下拉默认 DeepSeek）
- 模型（下拉选择 deepseek-v4-flash / deepseek-v4-pro）
- API 密钥（输入框 + 「获取 API 密钥」链接 + 显示/隐藏切换）
- 高级配置（可展开：temperature、max_tokens 等）
- 底部提示「连通性测试会发起一次真实请求，会消耗少量模型 Token」
- 按钮：取消 / 重置 / 添加模型（深色主按钮）

**选择自定义模型时**（标题「自定义模型」）：
- API 格式（下拉默认「OpenAI Chat Completions 格式」）
- 自定义请求地址（带「完整 URL」开关，提示不要以 / 结尾，/chat/completions 会被补充）
- 模型 ID
- 模型展示名称（可选，0/32 字符限制）
- API 密钥
- 高级配置（可展开）
- 同样的底部提示和按钮组

UI 风格：圆角卡片、灰色背景输入框、深色主按钮、红色星号必填、可展开高级配置、图标点缀。

---

## 十五、API 概览

- `POST /api/documents/upload` — 上传文件（multipart/form-data，支持多文件）
- `POST /api/documents/import-url` — URL 导入（抓取+解析，查重）
- `GET /api/documents` — 文件列表（支持状态筛选、分页）
- `GET /api/documents/{id}` — 文件详情
- `DELETE /api/documents/{id}` — 删除文件（级联删除 chunks、向量与相关缓存）
- `POST /api/documents/{id}/reparse` — 重新解析
- `POST /api/documents/{id}/unlock` — 提交 PDF 密码
- `POST /api/documents/batch` — 批量操作（删除/重新解析/入库）
- `POST /api/documents/{id}/chunk` — 触发切分（body 可带参数覆盖全局设置）
- `GET /api/documents/{id}/chunks` — 获取切分结果（分页、按类型筛选、关键词搜索）
- `GET /api/documents/{id}/chunks/{chunk_id}` — 单个 chunk 详情
- `POST /api/documents/{id}/index` — 确认入库（向量化+存向量库）
- `GET /api/documents/{id}/blocks` — 解析块预览
- `GET /api/documents/{id}/parse-report` — 解析质量报告
- `POST /api/documents/{id}/clean` — LLM 清洗（off/problem/full 档）
- `POST /api/documents/{id}/parse-variant` — 切换解析变体（raw/cleaned）
- `POST /api/search` — 检索测试（body: query, top_k, use_rerank, document_ids, chunk_types）
- `POST /api/evaluation/qasets` — 上传 QA 测试集（multipart，name 为表单字段）
- `GET /api/evaluation/qasets` / `DELETE /api/evaluation/qasets/{id}` — 测试集列表/删除
- `POST /api/evaluation/qasets/generate` — LLM 自动生成测试集
- `POST /api/evaluation/experiments` — 运行评测实验（独立向量表，Recall@k/MRR）
- `GET /api/evaluation/experiments` / `DELETE .../{id}` — 实验列表/删除（级联 drop 实验表）
- `POST /api/export/jsonl` — 导出 JSONL（alpaca/dify，章节过滤，strip_cot 思维链清洗）
- `GET /api/documents/{id}/chunk-stats` — 切分统计（token 分布、版本对比）
- `GET /api/stats` — 仪表盘统计（文档数、chunk 数、向量数、存储大小）
- `GET /api/settings` / `PUT /api/settings` — 配置读写
- `GET|POST|PUT|DELETE /api/settings/llm...` — LLM 配置管理 + 连通性测试
- `POST /api/settings/embedding/test` / `/reranker/test` — 模型连通性测试
- `GET /api/settings/models` + `/models/download[...]` — 模型下载管理（含进度/取消）
- `GET|DELETE /api/settings/cache` — 缓存状态/清理（按类型）
- `GET /api/health` — 健康检查（含 models 就绪状态）
- `WS /ws/tasks` — 任务进度 WebSocket（全局广播）

---

## 十六、工程约定

- 模型懒加载 + 启动预热：权重首次使用才载入（加载互斥锁防并发重复载入），lifespan
  启动后台预热已下载模型（首查不再冷等 40s+），`/api/health` 可观测就绪状态
- 文件 hash 缓存：同一文件不重复解析，调参只重切不重解析
- Windows 兼容：ProcessPoolExecutor 使用 spawn 模式，`if __name__ == "__main__"` 保护
- API Key 存 SQLite，靠操作系统文件权限保护
- 核心 chunker 模块必须有单元测试（参数→缓存 key 的映射有覆盖率守卫）
- 日志：Python logging，同时输出控制台和文件（data_dir/logs/），方便用户提 bug
- 中文路径：全链路 pathlib + UTF-8
- 异步任务：仅 Docling 解析走进程池（spawn，max_workers=2）；切分/向量化/检索在主进程
  共享模型单例，阻塞段包 asyncio.to_thread 避免卡事件循环
- 配置热更新：设置页改配置后后端实时生效，不需要重启服务
- 版本号：语义化版本（semver），MVP 为 0.1.0

---

## 十七、开发阶段

### Phase 1：核心引擎（无 UI）
- Docling 解析封装 + 缓存
- 八阶段切分算法（结构/语义/递归/Parent-Child）
- BGE-M3 向量化 + LanceDB/sqlite-vec
- SQLite 数据模型
- CLI 验证：`tessera ingest file.pdf` 能跑通解析→切分→向量化→存储→检索

### Phase 2：API 层
- FastAPI 骨架 + 所有 REST 接口
- WebSocket 进度推送
- LLM 客户端（DeepSeek + 自定义）
- Qdrant/pgvector 存储实现
- 设置管理

### Phase 3：前端
- React + Vite + shadcn/ui 脚手架
- 文档管理页
- 切分预览页（左右联动核心交互）
- 检索测试页
- 设置页（含 LLM 配置弹窗）

### Phase 4：打磨
- 错误处理完善
- 缓存管理 UI
- 单元测试补全
- 文档（README + 贡献指南）
- Docker Compose 一键启动

---

## 十八、MVP 范围

**做**：PDF/DOCX/PPTX/MD/TXT/HTML 解析、结构+语义+LLM+递归四策略切分、Parent-Child、BGE-M3 本地向量化 + OpenAI 兼容 API embedding、LanceDB/sqlite-vec/Qdrant/pgvector 四档存储、混合检索（稠密+稀疏 RRF + Rerank）、6 个 Web 页面（文档管理/解析预览/切分预览/检索测试/质量评测/设置）、WebSocket 进度、单知识库、用户可选数据目录。

**不做**：多知识库、桌面应用打包、多用户/权限、增量更新（文件变更只重切变化部分）、文件夹批量监控、对外 API 认证、EPUB/MOBI/音视频格式。
