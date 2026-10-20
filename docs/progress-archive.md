# Tessera 历史进展归档

> 由 PROGRESS.md 拆分而来（2026-10-10）：本文是 2026-08-12 至 2026-10-08 的
> 详细进展、质检记录、走查报告与已完成清单，供回查。
> **当前状态与待办见 [PROGRESS.md](../PROGRESS.md)**；bug 归因见 [PAIN_POINTS.md](../PAIN_POINTS.md)。

---

## 今日进展（10-08）：空信息块过滤开关 + 存量文档重解析验证

### 一、接手体检（只读）

- AGENTS §0 全流程走查：基线 `215 passed`（63.14s，后随本次功能增至 219）+ `tsc -b` exit 0；工作树干净，HEAD `8e9a42c`
- 库内 4 文档：课程论文.docx(indexed) / CCF 目录.pdf(indexed) / 参考文献著录规则.pdf(chunked) / Planning.txt(chunked)；chunks 536

### 二、存量文档重解析（PM 建议第 3 优先，消除"修了用户看不到"）

- 触发 CCF / 参考文献 / DOCX 重解析 + 4 文档重切 + 重入库（Planning.txt 无表格，未重解析）
- **table_data 修复确认生效**：重解析前 0/79 表格有结构化行列（缓存为 `{markdown, rows:[], headers:[]}`，生成于 08-15 前），重解析后 **79/79 全有**（CCF 72/72、DOCX 5/5、参考文献 2/2）
- **标题层级无需重解析**：`cache._apply_heading_inference` 在**读缓存时**惰性调用 `infer_headings`，而 `refine_heading_levels` 就在其末尾 —— 旧缓存 96 个全 L1 的 heading 读出来即为 65/16/15。09-07 PM 走查"标题层级对用户不可见"的结论已不成立（该结论基于更早的代码状态），链路三处（章节树/面包屑/父子块）均已正常
- 参考文献重解析后 blocks 615 → 515（-100）：08-14 的页眉页脚跨页重复行清理（`quality.remove_repeated_edges`）在旧缓存上从未生效，本次才真正落地
- 重解析会按设计清空旧 chunks/向量（`run_parse` → `clear_document_caches`），故需重切+重入库恢复一致状态

### 三、空信息块过滤开关（PM 建议第 1 优先，产品决策：用户拍板「给用户开关」）

**根因**：`structure.aggregate_section` 对 image 块写 `content = block.text.strip() or "[图片]"`；占位符非空文本 → 逃过 `merge_small` 的空块丢弃 → 照常向量化入库。

**实现**（默认开启=不入库，用户可在切分参数面板关掉）：
- `ChunkingConfig.drop_empty_blocks=True` + 混入 `config_hash`（旧切分缓存自动失效）
- `chunker.is_empty_info_chunk`：非 TEXT 块且内容为空或仅占位符 → 零信息；**表格仍有 headers/rows 时保留**（结构本身是信息）
- 过滤挂在**阶段四补**（overlap / merge_small / 父子块之前），零信息块不参与 overlap 前缀传播
- `structure.PLACEHOLDER_IMAGE` 常量集中定义，避免「[图片]」字面量两处漂移
- 前端 `ParamPanel` 新增「过滤空信息块」开关（复用既有 `SwitchField` 的 hint 位）

**验证**：
- `tests/test_chunker.py::TestDropEmptyBlocks` 4 例（默认丢弃 / 关闭保留 / config_hash 变化 / 谓词契约，含"表格有结构不丢"分支）；**219 pytest 全绿**（215+4）、`tsc -b` 零错误
- 真实数据：4 文档重切后占位符 `[图片]` 块 **27 → 0**（CCF 18 / DOCX 5 / 参考文献 3）；切分总数 536 → 423
- 端到端检索实测（8766，重解析前 PM 走查记录「参考文献著录格式」top5 全是 `[图片]`）：现 top5 全部为真实正文内容

### 四、同日追加修复：config_hash 漏字段 + 设置分组深度合并（commit c783221）

用户拍板「会给用户正反馈的改动就修，先修小改动」。两条都是「文档口径与实现不符」，属已修 5 处缓存失效缺口的同族。

**1. `config_hash` 漏掉 parent_chunk_size / child_chunk_size**（`core/types.py`）
- 后果：拖「父块大小 / 子块大小」滑块 → 点重新切分 → key 未变 → 命中旧切分缓存 → **参数静默不生效**，用户视角是「这个滑块是坏的」（与 PM 走查 A.2「min_chunk_size 参数承诺与结果不一致」同类信任问题）
- **先证实参数确实影响产物**（离线跑切分引擎，关语义阶段避免加载模型，用 CCF 缓存 blocks）：
  - 基线 1024/256：父块 38 / 子块 137，父块 token 中位 652、max 863
  - 只改父块 2048：父块 **27**、max **1846**
  - 只改子块 512：父块 **18** / 子块 **82**
  - → 旧行为确实是"改了参数拿回旧结果"，修复成立
- 新增 `TestConfigHashCoverage`：遍历 `ChunkingConfig.__dataclass_fields__` 逐字段改值断言 hash 必变 + 一条具名回归用例。**已反向验证守卫有效性**：临时删掉那两行后该测试立即失败并点名「字段 `parent_chunk_size` 未参与 config_hash」

**2. 设置分组：`load_settings` / `runtime.update` 是整体替换而非深度合并**（`services/settings_store.py`）
- `load_settings` 的 docstring 早已写「与默认值深度合并」，实现却是 `merged[row.key] = row.value`；`runtime.update` 同样整体替换 → **同一份持久化数据在重启前后生效配置分叉**（重启后补齐默认字段，重启前不会）
- 抽出 `settings_store.deep_merge`，两处统一走它；列表值（`llm_providers`）按整体替换，不做元素级合并
- **如实定性：这条当前无用户可见影响**。已逐条核实消费端（`app_cfg.get(...)` / `self.get("download").get(...)` / `ChunkingConfig.from_dict`）全部带默认值，且前端各设置面板（App/Rerank/VectorStore/Embedding/Download）始终提交完整分组。修它是消除陷阱，不是修一个正在发生的 bug
- 新增 `tests/test_settings_store.py`（9 例：缺键回落 / 嵌套合并 / 列表整体替换 / 非 dict 覆盖 / 不改基对象 / 落库旧分组向后兼容）+ `TestSettings` 2 例（部分更新不抹掉同组其它键、持久化的是合并后完整分组）

**验证**：**232 pytest 全绿**（219 + 2 hash 覆盖 + 9 settings_store + 2 api）、`tsc -b` 零错误
**副作用**：config_hash 口径变更使既有切分缓存成为孤儿（下次重切重算一次，约 1 分钟；DB 内 chunks 与检索不受影响）。未主动重切"暖缓存"——理由：产物内容不变，重切只会凭空多出一个统计快照版本（而 PM 走查已把"内部版本计数外露"列为界面问题）

### 五、检索质量问题（10-08 晚重新量化，修正此前结论）

> **修正**：此前第四节记的「稀疏通道仍未闭环：4 组查询 `sparse_rank` 全为 null」**是错的**——那是对单次响应里 top-1 一条的 `sparse_rank: null` 的误推。10-08 晚逐条实测 3 组查询的全部结果：`sparse_rank` **不为 null**，稀疏通道是通的，真正的问题是它**输出噪声**。

**实测（8766，use_rerank=false，top_k=5）**：

| 查询 | 结果构成 |
|---|---|
| 参考文献著录格式 | 1(dense#1) 2(**sparse#1**) 3(dense#2) 4(**sparse#2**) 5(dense#3) |
| 图像裁剪与镜像变换 | 1(dense#1) 2(**sparse#1**) 3(dense#2) 4(**sparse#2**) 5(dense#3) |
| 布尔代数的完备性 | 同上 |

- **每个 query 的第 2、4 位，`dense_rank=None`、`sparse_rank=1/2`，来源全部是 Planning.txt**
- 分数可反推通道：`0.016393 = 1/61`（dense#1 **和** sparse#1 同值）、`0.016129 = 1/62`。即每条结果的分数恰好是**单项**，top5 = 两个通道的 1/2/3 名交错 —— **Planning.txt 的纯稀疏命中与正确答案的纯稠密命中并列第一**

**根因**：`vector_store.fts_tokenize` 把 CJK 拆成**逐字**并保持 **OR 语义**（`参考文献著录格式` → `参 考 文 献 著 录 格 式`）。逐字 OR 下任何含任一常用字（与/的/是…）的长文本都会命中。离线实测：
- `图像裁剪与镜像变换` 的 FTS top3 里有一条**只命中「与」一个字**（score 1.63）也进了候选
- 对照组：`龘靐齉爩麤` → 0 条（确认确实在按字过滤，不是"全表返回"）
- Planning.txt 是长"文字墙"文档，单位 chunk 含字种类最多 → BM25 分最高 → **每查必占稀疏路 1、2 名**

**结论调整**：问题 4（Planning.txt 霸榜）与问题 6 是**同一个根因**，且**不在 chunker 也不在 embedding 表示质量**——是稀疏通道的汉字 OR 匹配把长文本变成了万能命中。此前"长文本块 embedding 表示质量"的归因方向不对。

**候选修法（需拍板，非确定性修复）**：
1. **bigram 分词**（治本）：`fts_tokenize` 改按 2-gram（「图像」「裁剪」），误命中率数量级下降。代价：`content_fts` 影子列与 FTS 索引需全量重建（数据量小，可接受）
2. **停用字过滤**：剔除高频字后再 OR，成本最低，但长文档仍占优
3. **稀疏路降权**（weighted RRF）：`w_s < 1` 压低噪声权重，不根治
4. **稀疏命中最低分阈值**：`_score` 从 1.6（噪声）到 39.6（强匹配）区分度明显，可作可配阈值
5. **只在稠密候选内精排**（稀疏不扩召回）：彻底消除稀疏引入无关文档，但丢掉稀疏的召回补充价值

### 六、审查报告第二批：bigram 分词 + C 方案 + 可观测性 + 评测集（commit c594c31）

**用户实测推翻了「稀疏阈值优先」**：正确答案文档连 FTS top15 都进不去（「参考文献著录格式」top15 全是 Planning，「刊物全称」CCF 零命中）。机制是**逐字 OR + BM25 长度归一化**——著录文档的块全库最长（表格密集）、长度惩罚最重；Planning 的中等长度文字墙每个常见字都蹭到分加总反超。**正确答案块的稀疏分系统性低于噪声，任何阈值都无法区分** → 稀疏侧阈值**不做**（避免设置项膨胀），只保留 rerank 阈值。

**一、bigram 分词（主修复）**
- `fts_tokenize`：CJK 连续段切**相邻二元**（段长 1 退单字），非 CJK 段逐字原样输出、由底层 unicode61 自行切词（否则 `FastAPI` 会被拆成 `F a s t A P I`）
- `FTS_TOKENIZER_VERSION = 2`（1 = 逐字已废弃）；8 个 golden 用例钉住行为，含段边界「图像API中文」→ `图像/API/中文`
- LanceDB 与 sqlite-vec **共用本函数**，写入/查询两侧自动一致；迁移通过**重入库**完成（upsert 按 content 重算影子列），无需一次性脚本

**二、可观测性：检索 API 返回 per-channel 分数**
- `ScoredChunk` / `SearchResultItem` / API / 前端检索卡新增 `dense_score` / `sparse_score`（各通道原始分）
- 只有 RRF 融合分时，每次调优都是盲调；本次立刻见效——直接看到 `sparse_rank=5~10` 而非 null，也看穿了交替模式的来源
- 顺带修 `api/search.py` 降级 Reranker 漏传 `min_score`（上一轮疏漏）

**三、评测集 `docs/retrieval_eval.json`**（每文档 3 条有答案 + 2 条全库无答案负例，共 18 条），可 `POST /api/evaluation/qasets` 导入复用自带评测页

**四、问题 3 走 C 方案 —— 且更正归因**
> **更正**：第五节原写「section 边界把标签段落和表格切开」**是错的**。真根因在**段落级拆分**，证据三条：
> 1. 标签块 `block_ids` 含 3 个表格 —— 因为碎片**继承单位级** `block_ids`（`chunker.py:224`），若是 section 边界切开则只有它自己
> 2. 标签块 `strategy="structure"` 正是 `flush_buf()` 给碎片的标记（`chunker.py:198`），不是单位属性
> 3. `merge_small` 只向后看（`merged[-1]`），标签前邻是上一 section 尾块、breadcrumb 不同 → 不合并
>
> 机制：CCF 单位 = 标签 + 3 个表格 markdown（table_handling=markdown 融入同一 TEXT 单位）→ 超 child_chunk_size → 按 `\n\n` 段落切，7 token 标签放不下后面的表格段而被单独吐出。

- `Chunker._merge_tiny_pieces`：token < `min_chunk_size` 的碎片作为**下一碎片的前缀**，连续小碎片累积；末尾无下一碎片则保持边界真实。**不做「是不是标签」的启发式**——任何小前导段（「见下表：」）一并兜住
- `CHUNKER_VERSION` `uffd-fix-1` → `tiny-frag-merge-1`

### 七、验收结果（真实库 4 文档重切 + 重入库，423 → 419 块 = 合并掉 4 个碎片块）

| 验收项（用户给的 golden 条件） | 结果 |
|---|---|
| 正例 hit@5 | **12/12 = 100%**（11 条 rank=1，1 条 rank=3） |
| 「参考文献著录格式」top5 来源 | **全为著录规则文档，Planning 退出 top5** |
| 「龘靐齉爩麤」 | 仍 **0 命中** ✔ |
| 段长 1 退单字（分词层） | 用例覆盖 ✔ |
| 单字**查询**兜底 | **0 命中 —— 见下方待拍板** |
| 负例噪声 | top5 仍返回结果，但 rerank 分 0.010 / 0.0005 / 0.00009 |

**重要区分**：raw FTS 侧「参考文献著录格式」top3 仍是 Planning（BM25 长度归一化未解），但著录块已进稀疏 5~10 名 + 稠密 1/3/6/9 → **融合后 top5 全为正确答案**。即目标「Planning 不进 top5」在 API 层达成，"FTS top3 全对"这句只在 raw 通道层未达成。

**负例的正解是 rerank 阈值**（通道分数实测）：正例 top1 `0.989 / 0.993`，负例 top1 仅 `0.010`，**差百倍** → `min_score ≈ 0.1` 即可让负例返回空、不影响正例（ReAct 正例的 0.363 也保住）。这是 min_score 该派的用场。

### 八、待拍板（两项）

1. **单字中文查询返回 0 条**：bigram 索引里没有单字 token，所以「献」这类查询必然 0 命中（旧逐字索引能命中但全是噪声）。分词层的"段长 1 退单字"是对的（混合文本如「图像API中」会产出 `中`），缺的是**查询层**兜底。可选：① 接受现状 + 前端提示「请至少输入 2 个字」（成本最低）② 稀疏检索 0 命中时回落 LIKE 子串扫描（419 行无压力，规模上来有代价）③ 不管（单字查询本就无信息量）
2. **min_score 默认值**：建议把默认从 0（不过滤）改为 **0.1**——实测正例 0.99 / 负例 0.01，区分度百倍。但改默认值就改变了默认行为，需你点头

### 九、P1 双件套落地（审查官拍板后执行，commit a53bc13）

**两项拍板结论**：① 单字查询选 **c（不管）**——b（LIKE 兜底）与②硬冲突，LIKE 命中的块过 rerank 分数必然落在负例区间，开阈值后照样被滤空，等于白做；② min_score 默认 **0.1 批准**，前置条件：先对齐 top_n。

**一、top_n 持久化值 5 → 20（数据改动）**
- 09-07 PM 记录「设置页 top_n=5 与 PRD P0-9 top20 矛盾」**属实**：`settings` 表持久化值是 5，而 schema / DEFAULT_SETTINGS 默认值都是 20。上一轮只核对默认值就断言「记录不成立」，属误判，本次更正
- 用 `json_set` **只改 top_n 一个键**，故意不写入 `min_score` —— 一旦落库显式值，deep_merge 会让此后改默认值不再生效。改后 `GET /api/settings` 确认 `top_n=20` 生效

**二、min_score 默认 0 → 0.1**
- 同步 `DEFAULT_SETTINGS` / `RerankerConfigSchema` / `Reranker.__init__` 三处
- `runtime` 取值**不用 `or` 兜底**：用户显式设 0（不过滤）必须保留，否则会被默认值改写。新增 `test_reranker_explicit_zero_min_score_preserved` 钉住
- 前端输入框标注校准于 bge-reranker-v2-m3、0 = 不过滤、建议 0.1、换 reranker 模型需重新校准

**三、空态语义区分**
- 新增 `threshold_dropped`（被阈值滤掉的候选数）贯通 SearchResponse → API → 前端
- `results` 为空且 `threshold_dropped>0` → **「无足够相关的结果」**；与「无召回结果」（真没匹配）分开；弱命中仍渲染 ResultCard（带分数条）——三者视觉各不相同
- 负例与单字查询自然落同一条空态（实测单字「献」→ `results=0 / threshold_dropped=10`）

**四、守护测试**
- `tests/test_eval_gate.py`：评测回归门，需 `TESSERA_API` 环境变量，默认跳过（不污染离线套件）。负例失败会先查 `used_rerank`，提示「请先启用 rerank」而非误报检索退化
- golden 单字用例改写钉语义：单字查询稀疏 0 命中是**预期行为**，不崩、不返回伪命中、不污染排序；写明「别当 bug 修成 LIKE 兜底」及其与 rerank 阈值的硬冲突
- 顺手：`_merge_tiny_pieces` 删死分支 `if carry:`；`docs/retrieval_eval.json` 补 `precondition` 字段，正文措辞对齐实际 18 条（12 正 + 6 负）

**验证**：255 pytest 全绿 + 3 skipped、`tsc -b` 零错误；真实库回归门 **3 passed**（正例 12/12 hit@5、负例 6/6 全空）；延迟 min 179 / 中位 223 / p95 391 ms（top_n 5→20 精排量 ×4 未见恶化，09-07 基线 8.3s 含模型加载）

**附带发现**：`PAIN_POINTS.md` 非本轮撰写，随提交带入；其 v3 归因我已独立验证成立——6 个残留标签块中 **5 个后继 block 是 image**、1 个是 heading（idx=29），即 `heading → 段落标签 → image → table`，`aggregate_section` 遇 image 触发 `flush_text()` 让标签孤立成 unit，随后该 image 又被 `drop_empty_blocks` 丢弃，形成「**幽灵打断**」（打断者自己消失了）。这比我上一轮的 v2（"段落被表格打断"）更准。**修复方向因此改为**：空图片 block 的丢弃提前到聚合之前（`normalize_blocks`），而非在碎片层做合并——P3，待排期

## 今日进展（09-07 晚）：5 处缓存失效缺口全部修复（PM 建议第 2 优先）

**背景**：09-07 PM/UX 走查 A.3 坐实——标题层级修复已提交但旧解析缓存不失效，用户界面仍全 H1，"修了用户看不到"。5 处缺口全部按 PROGRESS 根因记录修复：

1. **高｜重解析后切分缓存未失效**：`run_parse` 清理旧产物处加 `clear_document_caches`（reparse/unlock/批量重解析同走此路，一处全覆盖）
2. **中｜清洗版切分缓存不区分清洗参数**：`cache.clear_chunk(file_hash, variant)` 新方法；`run_clean` 写 cleaned 缓存后清 cleaned 变体切分缓存（raw 变体保留），随后自动重切走新产物
3. **中｜WS 终态缺 cleaned**：`FINAL_STATUS` 加 `cleaned`，终态额外 invalidate `parse-blocks`/`parse-report`，toast 补"清洗完成"文案
4. **低｜embedding 缓存**：key 混入 `_EMBED_CACHE_VERSION`；`clear_embed(text_hash)` 清所有模型/版本变体；`_delete_document` 删 chunks 前收集 content hash 集合，删后逐个清理（跨文档共享缓存的误伤代价=重算一次，注释说明）
5. **低｜useDocument 无轮询**：加 `refetchInterval`（中间态 3s，与列表页一致）

**验证**：新增 6 个回归测试（test_cache.py×3 / test_tasks.py×2 / test_api.py×1），**215 pytest 全绿**（209+6）；前端 tsc 零错误。**后续动作**：现有 4 个文档需重新解析+重切才能让标题层级/table_data 修复生效（缓存缺口已修，重解析现在会正确失效旧切分缓存）。

## 今日进展（08-17）

### P0 切块重复/相邻重叠修复（chunker + structure，已验证待提交）

**问题**（08-15 质量检查）：19/227 chunk 内相同行重复出现（如"周易外传：卷5..."x2）；51 对相邻 chunk 重叠 22%，其中 9 对整段重叠而非滑动窗口。真实数据复测：34 chunk 内部重复行、30 对相邻重叠、9 对 ≥80% 整段重叠。

**根因**（分段实验实锤：阶段 1-4 后 0 重复 → overlap 后出现 → merge 后放大）：
1. **overlap+merge 复合**：阶段六 `apply_overlap` 给小块 B 灌入前块尾部前缀，阶段七 `merge_small` 又把 B 并回前块 → 同一内容在合并后的 chunk 内出现两次
2. **短块整段复制**：prev ~60 token 时 50 token overlap tail 覆盖其全部内容 → 相邻 chunk 整段重叠；且 curr 原文过短时被前缀灌大到逃过 `min_chunk_size` 合并
3. **overlap 滚雪球**：tail 从已含前缀的 prev 计算，逐块累积传播（同一行出现 3-4 次的来源）
4. **源 blocks 相邻重复**：OCR 页眉/页脚重复捕获

**修复**（`core/chunker.py` + `core/structure.py` + `core/parent_child.py`）：
1. `apply_overlap`：prev **和** curr 原文均需 `token >= overlap*2` 才加前缀（对称 skip 规则）；tail 基于前块**原始内容**（剥离其自身 `_overlap_prefix`）计算防累积；前缀 `tail.strip()` 去前导空白（token 切点落在换行后剥离会失效）并记录在临时属性 `_overlap_prefix`（Chunk 无 slots，asdict 不序列化）
2. `merge_small`：合并前剥离被合并块的 `_overlap_prefix`（prev 末尾已含同样文本）
3. `aggregate_section`：相邻同文本段落去重（OCR 页眉/页脚）
4. `parent_child._child_content`：父块拼接子块时剥离子块 overlap 前缀，否则前块末尾文本会在父块中部重复出现

**验证**：真实数据（参考文献 PDF 615 blocks）重切 188 chunks：内部重复 34→**0**（仅剩"原题：Li Jiangning"为源文档真实重复，两示例条目同名，合法）、整段重叠 9→**0**、相邻重叠对 2（正常滑动窗口 overlap 设计）；新增 5 个回归测试（含父块中部重复检测），188 pytest 全绿。

**重切实测注意**：DB 中旧 chunk 为陈旧数据，需重切才生效；切分缓存失效缺口未修（见已知缺陷），本次验证手工删了 `chunk_8d6460ea*` 缓存文件强制走新代码。

### Planning.txt 原文对照验证 + overlap 前缀 U+FFFD 修复（08-17，已验证待提交）

**对照方法**：归一化（去空白）后把每个子块在原文中定位，做区间合并算覆盖率，检查相邻重叠/边界类型/token 分布/父块重复行。

**对照结果**（59 块 = 12 父块 + 47 子块/独立块）：
- 正文 100% 覆盖无丢失；12 处"缺口"全是标题行——标题被提取进 breadcrumb/heading_text 元数据（13/13 完整），markdown `##` 标记被剥离，属设计而非丢失
- 相邻重叠均为 50 token 滑动窗口设计；chunk[20]→[21] 检出 167 字"重叠"是原文 `====` 分隔线自身重复（后缀匹配被放大），非 bug
- 父块"重复行"仅 `====` 分隔线（原文就有 4 处），忠实还原非 bug
- token 分布健康：中位 224 / 均值 204 / max 377（< 1.5x child_chunk_size 上限）；<50 token 小块仅 2 个（文档首块、章节首短段，均为边界合法情形）
- 推理过程"文字墙"段落内切点落在句中（24 处）——超长单段递归兜底的预期行为；结构化回复部分切点全部落在段落边界

**发现并修复的真 bug——overlap 前缀 U+FFFD 污染**：
- 现象：4/47 子块（chunk[14]/[18]/[31]/[40]）无法在原文定位，开头带 U+FFFD；覆盖率因此虚降至 91.2%
- 根因：`tokenizer.truncate_to_tokens` 用 tiktoken 切片取 overlap tail 时，切点落在 byte-fallback 多字节 token 中间，`decode` 把残缺字节还原为 U+FFFD，随前缀灌入后续 chunk 内容（修复前复现：chunk[13] 尾部 50 token 截断即产出 `\ufffdAI认证...`）
- 修复：decode 后剥离切点侧 U+FFFD（from_tail→lstrip / 否则 rstrip）；U+FFFD 只可能出现在切点一侧边缘，剥离安全
- `CHUNKER_VERSION` "dedup-fix-1"→"uffd-fix-1"（混入 config_hash 使旧切分缓存自动失效）
- 验证：新增 `TestTruncateNoReplacementChar` 2 例（全截断点穷举 + overlap 前缀集成；测试文本在旧行为下 64 个截断点产 U+FFFD，确实能抓住回归）；**190 pytest 全绿**；重启后端重切 Planning.txt 复测：内容保真 43/47→**47/47**，覆盖率 91.2%→**97.29%**（剩余即标题元数据缺口）

### 预览页右栏排序修复 + 联动改单向（08-17，已浏览器实测，未提交）

**问题一：右栏 chunk 顺序与原文对不上（相邻块在原文不相邻）**
- 根因：chunk_index 是两套独立编号空间（顶层块=父块+独立块按文档流编号，子块在 0..M 另行编号；后端有意设计便于 API role 过滤），前端 `useAllChunks` 只按 `chunkIndex` 单字段排序，两套空间交错混排（Planning.txt 实测 **32 处逆序**）
- 修复：`useChunks.ts` 新增 `sortByDocumentFlow`——顶层块按顶层编号排序，每个父块后紧跟其子块（按子块编号）；重排后逆序 **0 处**（用"每个 chunk 首 block 在解析 blocks 中的位置单调不减"验证）

**问题二：联动方向调整（用户要求）**
- 移除左→右：左栏滚动不再驱动右栏列表滚动/高亮（删 handleVisibleLeft + lastLink 防回环逻辑；SourcePanel 的 IntersectionObserver 仅保留面包屑跟随）；左栏悬停也不再点亮右侧卡片
- 保留右→左：右侧点击→左侧滚动+闪烁；右侧悬停→左侧区间高亮；章节树跳转仍双向定位

**浏览器实测（5173，Planning.txt）**：A 右栏排序符合原文流 PASS；B 左栏滚动/悬停不再驱动右栏 PASS；C 右→左点击闪烁+悬停高亮 PASS；D 章节树跳转左右栏同步 PASS；E 控制台无应用报错（2 条 SyntaxError 来自浏览器自动化注入脚本）。tsc --noEmit 零错误。

### 切分预览页顶栏按键实测（08-17，全部正常）

浏览器逐项实测 `/preview` 顶栏 7 个交互元素：文档选择器（切换文档 chunk 列表正确刷新）、切分参数（展开/收起 6 滑块+2 开关+表格下拉）、统计（token 直方图 v3/v2 对照+类型环图+均值卡片）、LLM 辅助（关/保守/标准三档弹窗）、导出 JSONL（alpaca/Dify 模板+章节过滤弹窗）、重新切分（POST /chunk 成功重切）、确认入库（POST /index 向量化完成，chunk 出现"入库"标签）。无控制台报错、无失败请求。副作用：测试中 Planning.txt 被重切+入库，其 43 块版本（今日 12:19 非默认参数临时态）恢复为默认参数的 63 块（与 08-15 v1 同 config_hash），无害。

### P0 table_data 空修复完成（09-07，已提交）

- 根因：HEAD 版 `parser.py` 写死 `"rows": [], "headers": []` 从未提取结构化数据
- 修复（`core/parser.py`）：
  1. `_extract_table_grid`：export_to_dataframe 优先 + table_cells 网格兜底
  2. **RangeIndex 伪表头修复**：无表头表格 docling 列名退化为 RangeIndex(0,1,2…)，判型置空，首行回归数据（两条路径同步修）
  3. **跨页续表表头继承** `_inherit_continuation_table_headers`：页码相邻+列数一致时，无表头续表继承上一表格块表头；首行数据被误判表头的（序号列纯数字特征，CCF p55 实测）降级回数据行再继承。**注意必须在分批合并后的完整 Block 流上调用**（PDF 每批 10 页，批内调用漏跨批续表）——调用点在 parse_with_meta
  4. `_clean_table_cells`：清洗 docx 加粗 `**` 标记；表头全空置空
- 验证：新增 10 个单测（DataFrame/RangeIndex/网格兜底/加粗清洗/续表继承×4），**200 pytest 全绿**；实测 CCF 72/72、DOCX 5/5 表格有结构化数据，9 个无表头续表全部继承成功，p55 误判表头已纠正
- 已知小瑕疵：混合内容过渡页（一页含前表尾+新表头）后的续表会继承前一类的表头文本（会议/刊物仅一列名之差），结构正确，语义噪声可接受
- DB 中旧 chunk 的 table_data 仍为旧数据，重切后生效

### P1 标题层级全 level 1 修复（09-07，已提交）

- 根因：`_doc_to_blocks` 取 docling item level 恒为 1；`infer_headings` 只处理 PARAGRAPH，跳过已判 HEADING 块
- 修复（`core/heading_infer.py`）：新增 `refine_heading_levels`，在 `infer_headings` 末尾对已判 HEADING 块按序号重算层级：
  - 数字序号（「4.1 专著」→2、「4.1.1 xxx」→3），允许无空格紧跟 CJK（OCR 丢空格：「1范围」「4著录项目」）；无空格紧跟非 CJK（「1.5x」）或量词字（「1级」）不动
  - 中文序号「一、」/「第X节」→ 2、「第X章」→ 1
  - 无序号标题（术语条目/栏目名）保持原层级——文本规则无法可靠分级，需字号信息（后续可做）
- 验证：新增 9 个单测，**209 pytest 全绿**；真实 blocks 实测：参考文献 96 标题 {L1:65, L2:16, L3:15}、CCF 48 标题 {L1:25, L2:23}
- 注意：旧解析缓存的 blocks 仍是全 L1，文档需**重新解析**才生效（解析缓存不含解析器版本，属已知缓存失效缺口）


## 今日进展（08-15）

### PDF 多页解析漏字修复：分批转换解决 std::bad_alloc

**问题**：用户上传两份 PDF（参考文献著录规则 17MB/26页、CCF 目录 73页），解析结果大量漏字。

**根因**：docling-parse（Rust 后端）预处理阶段一次性渲染所有页面，多页 PDF 内存累积导致 `std::bad_alloc`，后续页面静默失败（错误记录在 `ConversionResult.errors` 但 Tessera 未检查）。实测：
- CCF 目录 73 页：默认配置只成功 18-40 页，35-55 个 bad_alloc 错误
- 参考文献著录规则：旧缓存仅 23 blocks/2429 字符（11 页），实际 26 页/56433 字符

**修复**（`core/parser.py`）：
1. **PDF 分批转换**：新增 `_parse_pdf` / `_convert_pdf_batches`，用 pypdfium2 获取总页数，每批 10 页转换后合并 blocks，避免内存累积。73 页 PDF 实测 0 bad_alloc / 51s / 133516 字符全页覆盖
2. **显式 PdfPipelineOptions**：`_build_converter` 始终传 PdfPipelineOptions（之前不传走 docling 默认 do_ocr=True 行为不确定）；数字版 do_ocr=False，扫描件 do_ocr=True + RapidOcrOptions(lang=["chinese"], backend="onnxruntime") + images_scale=2.0
3. **转换错误检查**：分批转换后检查 `result.errors` 并记录日志
4. **Docling 路径元数据补充**：`parse_with_meta` 中 infer_headings 后调 `extract_metadata(blocks, docling_meta=meta)`，修复 PDF/DOCX/PPTX 无内嵌元数据时 title/lang 全 None 的问题

**验证**：183 pytest 全绿；两份 PDF 重新解析完整（参考文献 615 blocks/56433 字符/26 页，CCF 167 blocks/133516 字符/73 页）。

## 今日进展（08-14）

### 两个真 bug 修复（第一批，已提交）
1. **LanceDB upsert 假更新**（`core/stores/lancedb_store.py`）：表存在时原走 `tbl.add`（append），store_failed 重试会追加重复向量。改为 `merge_insert("id").when_matched_update_all().when_not_matched_insert_all().execute()` 幂等写入，重试不产生重复行；已建 FTS 索引的表在 upsert 后 `create_fts_index("content_fts", replace=True)` 重建，保证 sparse_search 不陈旧。
2. **embedding 切换不重建向量**（`api/settings.py` + 前端 `EmbeddingPanel.tsx`）：检测 `provider_type/model_name` 变化，有存量向量时返回 `CODE_EMBED_REBUILD_CONFIRM`（带受影响文档数），前端弹确认框，确认后清向量库 + 重置文档状态为 chunked。PRD P0-6"切换模型强制确认重建"落地。

验证：新增 `tests/test_stores.py`（LanceDB 真实集成测试，覆盖 upsert 幂等 + delete_by_document），后端 **162 pytest 全绿**，前端 tsc 零错误。

### RapidOCR 路径修复（待办 2）
- 用 docling 内部解析逻辑（`_resolve_rapidocr`/`_rapidocr_artifacts`/`_backend_to_engine_type`）确定期望文件，把 rapidocr 包内 3 个 onnx 模型复制到 `docling/RapidOcr/`
- 补装 `onnxruntime 1.28.0`（venv 缺失）
- 验证通过：`RapidOcrModel init OK, reader ready: True`

### tableformer 下载完成（待办 1）
- 见下方"下载踩坑记录"，下载方法已沉淀到 `MODEL_DOWNLOAD.md`

### 扫描件 PDF 端到端验证完成（待办 5）
- 用真实扫描件（`D:\1RAG\ocr_sample_homework.pdf`，172KB）验证
- 撞到新硬卡点：docling layout 模型（transformers rt_detr_v2）触发 `torch.compile`，Windows 无 triton（仅 Linux）直接失败，报 `Cannot find a working triton installation`
- **解法**：`main.py:82` 加 `DOCLING_INFERENCE_COMPILE_TORCH_MODELS=0`（docling pydantic settings 环境变量，`env_prefix=DOCLING_`），关闭模型编译走 eager 推理
- 验证通过：RapidOCR 3 个 onnx 从 docling/RapidOcr/ 加载，解析 43 blocks / 2077 字符，识别出标题「数字图像处理 - 课堂作业」，耗时 17.1s

### 表格默认值 preserve→markdown（待办 4，完成）
- 后端 `types.py` + `schemas.py` 的 `table_handling` 默认值 `"preserve"`→`"markdown"`；前端 `usePreviewStore.ts` 的 `DEFAULT_CHUNKING.tableHandling` 同步改 markdown（前后端必须一致，否则前端仍回传 preserve 覆盖后端默认）
- 表格默认融入正文聚合（不再碎片化独立成块），符合 PROGRESS 82 行结论；preserve 保留给表格密集型文档手工选择
- 测试：`test_structure.py` 新增 `test_default_table_handling_is_markdown`（防回归）；`test_structure.py::test_table_code_image_independent` 和 `test_chunker.py::test_basic_pipeline` 显式设 `preserve` 保持原测试意图
- 后端 **163 pytest 全绿**（+1），前端 tsc 零错误

### 阶段一待办 3/6/7/8 收尾（今日第二批，完成）
**待办 3：TXT 编码自动检测**（`core/parser.py` + `pyproject.toml`）
- 新增 `_read_text_auto` / `_decode_cjk`：先 `utf-8-sig`（顺带剥 BOM）→ 严格尝试东亚编码候选链（gb18030 覆盖 GBK/GB2312 → big5 → shift_jis → euc_kr，仅接受无 U+FFFD/控制符的「干净」解码）→ charset_normalizer 兜底 → UTF-8 宽容
- **踩坑**：charset_normalizer 对短中文文本易误判（实测把 GBK 当 cp949 韩国码），故东亚编码显式候选排在它之前；把 charset_normalizer 从「主力」降级为「兜底」
- `pyproject.toml` 显式声明 `charset-normalizer>=3.0`

**待办 6：页眉页脚重复行自动清理**（`core/quality.py` + `parser.py`）
- `quality.py` 新增 `remove_repeated_edges(blocks)`：复用 `_repeated_edge_keys` 检测口径，仅删「每页首个/最后一个、且文本与跨页重复 key 一致」的正文块，避免误删正文正常重复内容；无重复时原样返回
- `parser.parse_with_meta` 在 Docling 路径末尾调用；纯文本/HTML 因 page=1 天然不触发

**待办 7：文档元数据提取填充**（新增 `core/metadata.py` + parser + tasks）
- `metadata.py`：`detect_language`（按汉字/假名/谚文/拉丁字符占比，zh/ja/ko/en）、`extract_title`（首个 level1 标题→首个标题→首个较短正文）、`extract_metadata`（规则提取 + Docling 元数据合并）
- `parser` 增 `parse_with_meta()`（保持 `parse()` 签名不变，兼容既有调用）+ `_extract_docling_meta`（读 doc.attribs / DocumentMetadata 的 title/author/lang）
- `tasks._parse_entrypoint` 返回 `(blocks, meta)` 元组；`run_parse` 解析后写入 `doc.title/author/lang`；缓存仍只存 blocks 列表，格式不变（旧缓存向后兼容）

**待办 8：URL 导入**（`api/documents.py` + 前端 `UrlImport.tsx`）
- 后端 `POST /api/documents/import-url`：httpx 流式抓取（超时 30s + 大小上限 + 浏览器 UA + scheme 校验 http/https）→ 按 Content-Type 落扩展名（HTML/PDF/DOCX/PPTX/MD/TXT）→ 复用 `save_upload_stream` 流式落盘 + SHA256 → 查重（exists/overwrite）→ 登记 Document 复用现有解析链路
- 用 `_ByteStreamReader` 把 httpx `aiter_bytes()` 适配到既有 `save_upload_stream` 的 `read(size)` 接口，不重复造轮子
- 前端文档管理页新增 URL 输入框 + 导入按钮 + 重复确认弹窗

**验证**：新增 `tests/test_parser.py`（15 例：编码/BOM/GBK/乱码兜底/空文档/页眉页脚清理与误删防护/语言判定/标题与元数据合并/md 元数据）+ `tests/test_api.py` 的 `TestUrlImport`（3 例：mock httpx 抓取建档/重复/非法 scheme/文件名映射）。后端 **181 pytest 全绿**（+18），前端 tsc 零错误。
**举一反三补充**：
- 修复 `_ByteStreamReader` 依赖 `from __future__ import annotations` 才未炸的 `Any` 未导入问题（补 `from typing import Any`）
- **修复纯文本/md/html 元数据不提取缺口**：`parse_with_meta` 对 `.md/.txt/.html` 原本直接返回空 meta，导致这些格式 title/lang 永远填不上（待办7 只覆盖了 Docling 路径）。现统一走 `extract_metadata` 规则提取，新增 `test_md_parse_with_meta`。`infer_headings` 只处理 PARAGRAPH 块、幂等，HTML 双重调用安全。
- 审查确认**非问题**：URL overwrite 分支（`save_upload_stream` 序号命名，新文件落 `_N` 后缀、旧文件被 `_delete_document` 删除，无新旧文件冲突）；`_decode_cjk` 的 gb18030 场吞损坏 UTF-8（东亚文档工具下 GBK 优先是合理权衡）；URL 导入无 SSRF 防护（本地单机工具，低风险）；重解析后 meta None 字段保留旧值（title 提取不到保留 filename stem 是有意 fallback，重解析内容不变故实际不触发）。

### 时间戳显示偏差 8 小时（08-14 修复，新增待办7 之外的回归）

**现象**：上传文件后列表右侧时间戳与真实本地时间不一致（晚 8 小时）。

**根因链**（实测验证）：
1. 后端 `db.py` 存 `datetime.now(timezone.utc)`（aware UTC）
2. SQLite 不保留时区，读回 tzinfo=None（naive）
3. Pydantic `DocumentOut.created_at: datetime` 序列化 naive → ISO **无 Z**（实测 `2026-08-14T09:37:01`）
4. 前端 `formatTime` 用 `new Date(iso)` 按**本地时区**解析无 Z 字符串 → 显示 UTC 数值，比真实本地时间晚 8 小时

**修复**：`schemas.py` 定义 `UTCDT` 类型（`PlainSerializer`，naive 补 UTC 输出带时区），替换全部 6 处 datetime 字段（DocumentOut×2 / QASetOut / ExperimentOut×2 / ChunkStatsOut）。前端 `formatTime` 无需改（带时区输入本就正确转换）。新增 `tests/test_schemas.py`（2 例防回归）。后端 **183 pytest 全绿**（+2）。

## 今日进展（08-12）

### 工作流改造（commit 53737ef）
- 解析完成后停在 `parsed` 状态，用户在解析预览页验收/LLM 清洗后手动触发切分，不再自动切分
- 修复父块 chunk_index 排序 bug：之前父块全部排在独立块前面导致章节顺序错乱，改为顶层块按文档流统一编号
- 新增 CHUNKER_VERSION = "order-fix-1"，混入 config_hash，排序逻辑变更后旧切分缓存自动失效
- 重解析时清理旧 chunks/向量数据，防止脏数据残留
- WS 推送后前端自动跳转：解析完成→解析预览页，切分完成→切分预览页
- 文档列表新增"开始切分"按钮

### 切分预览原文面板修复（commit 78771f1）
- 问题：左侧原文面板用父块 chunk 渲染，父块 content 是子块纯文本拼接，表格显示 markdown 源码、图片显示 `[图片]` 文字，与解析预览页视觉不一致
- 修复：左侧改用 `/documents/{id}/blocks` API（解析原始 Blocks），直接复用 BlockCard 组件渲染，表格/图片/代码各归其类
- 左右联动改为 block↔chunk 映射（chunk.blockIds），替代之前的父块映射
- SectionTree 改为从 Blocks 构建章节树
- 同时合入了 table_handling 三模式（preserve/markdown/skip）的实现

### 业务分析
- 从用户、产品、业务经理三角度完成两轮痛点分析（格式处理、产品缺失、竞品差距）
- 磁盘占用对比：Tessera 11.91GB（.venv 5.23GB + 模型 6.5GB），清理了 reranker 目录 1900MB `.incomplete` 残留
- 表格策略讨论结论：默认 preserve 不合理，表格独立成块 embedding 质量差（碎片短词无上下文），默认应改 markdown 融入正文；preserve 留给表格密集型文档

## 当前待办（阶段一：解析质量加固）

> 阶段一全部完成（08-14）。剩余阶段的待办见下方「四阶段规划」。

1. ~~docling tableformer 模型下载~~（**已完成 08-14**，见下方踩坑记录）
2. ~~RapidOCR 文件复制~~（**已完成 08-14**，已复制到 docling/RapidOcr/ 并验证初始化）
3. ~~TXT 编码自动检测~~（**已完成 08-14**，charset-normalizer + 东亚编码显式候选链，见今日进展）
4. ~~表格 table_handling 默认值 preserve→markdown~~（**已完成 08-14**，前后端默认值同步 + 测试）
5. ~~扫描件 PDF 端到端验证~~（**已完成 08-14**，见今日进展；triton 解法 `DOCLING_INFERENCE_COMPILE_TORCH_MODELS=0` 已固化到 main.py）
6. ~~页眉页脚重复行自动清理~~（**已完成 08-14**，见今日进展）
7. ~~文档元数据提取填充~~（**已完成 08-14**，解析时写入 title/author/lang）
8. ~~URL 导入功能~~（**已完成 08-14**，后端抓取+解析，前端输入框）

### 下载踩坑记录（08-14，tableformer + RapidOCR）
- **hf-mirror 对 docling 大模型返回 CAS 401**：新版 docling 大模型（layout onnx 变体、tableformer）已迁移到 HF 的 xethub 存储，hf-mirror 镜像对该 CAS 服务（cas-server.xethub.hf.co）返回 401 Unauthorized。报错形如 `CAS Client Error: 401`。
- **解法**：设 `HF_HUB_DISABLE_XET=1` 禁 Xet 协议，回落标准 HTTP 下载（已实测有效）。该修复已同步到 `model_manager.py` 的 `download_docling_bundle()`（含环境变量恢复）。
- **注意**：tableformer 的 revision 是 `v2.3.0`（非 main），模型路径 `model_artifacts/tableformer/{accurate|fast}/`，含 accurate（203MB）+ fast（139MB）两个权重。
- **后台进程**：Start-Process 起的 python 会被 WorkBuddy 外部机制周期性清理，须用 `schtasks /create + /run` 一次性任务独立运行。

## 已知缺陷（2026-08-14 代码复查记录；**2026-09-07 全部修复**，215 pytest 全绿）

按严重度排序。均为缓存失效缺口，会导致"数据更新后页面仍停在旧缓存"。

### 高：重解析后切分缓存未失效，切分命中旧结果 —— **已修复（09-07）**
- 现象：修复解析器（tableformer/RapidOCR/解析逻辑）→ 重新解析 → 文件 hash 未变 → 手动"开始切分"仍返回旧块。
- 根因：`clear_document_caches` 只在删除文档时调用；`run_parse` 重解析后只删 Chunk 表+向量，未清切分缓存文件。切分缓存 key 只含 file_hash+variant+config_hash，不含解析器版本。
- **修复**：`run_parse` 清理旧产物处加 `runtime.cache.clear_document_caches(doc.file_hash)`（reparse/unlock/批量重解析均走 run_parse，一处覆盖）；回归测试 `test_run_parse_clears_chunk_caches`。

### 中：清洗版切分缓存不区分清洗参数（text_scope / clean_table）—— **已修复（09-07）**
- 现象：先用 `text_scope=problem` 清洗再改 `full` 清洗，切分仍命中第一次的结果。
- 根因：`config_hash` 只含切分参数+CHUNKER_VERSION，不含 text_scope/clean_table；不同清洗参数产物都写同一 `parse_{hash}_cleaned.json`，切分缓存 key 相同。
- **修复**：`cache.py` 新增 `clear_chunk(file_hash, variant)`；`run_clean` 写完 cleaned 缓存后清 cleaned 变体切分缓存（raw 变体保留可复用），随后自动重切必然走新产物；回归测试 `test_run_clean_clears_cleaned_chunk_cache`。

### 中：前端 WS 终态集合缺 `cleaned`，清洗完成缓存刷新依赖页面内检测 —— **已修复（09-07）**
- 现象：清洗时不在解析预览页（如停在文档管理页），`parse-blocks/parse-report` 缓存滞留到下次挂载。
- 根因：`useWebSocket.ts` 的 `FINAL_STATUS` 无 `"cleaned"`；刷新靠页内 `useEffect` 检测才触发。
- **修复**：`FINAL_STATUS` 加 `cleaned`；cleaned 终态额外 invalidate `parse-blocks`/`parse-report`；toast 文案补"清洗完成"（EXTRA_STATUS_LABEL，cleaned 非 DocStatus 枚举不进 DOC_STATUS_META）。

### 低：embedding 缓存删除不清理 + key 不含模型版本 —— **已修复（09-07）**
- 现象：删除文档后 embedding 缓存残留；embedding 模型升级但 `model_name` 未变则命中旧向量。
- **修复**：`embed_path` key 混入 `_EMBED_CACHE_VERSION` 常量（模型实现变更时递增）；`cache.py` 新增 `clear_embed(text_hash)`（清所有模型/版本变体）；`_delete_document` 删 chunks 前收集 content hash 集合，删除后逐个 clear_embed（embed 缓存跨文档共享，同文本他文档缓存一并失效，代价是重算一次，注释已说明）。回归测试 `test_cache.py`×3 + `test_delete_document_clears_embed_cache`。

### 低：单文档详情 `useDocument` 无轮询、WS 中间态不失效 —— **已修复（09-07）**
- 现象：依赖单文档详情的页面在文档进入中间态（parsing/chunking…）时不刷新。
- **修复**：`useDocument` 加 `refetchInterval`（中间态 3s 轮询兜底，与列表页口径一致）。

## 解析+切块质量检查（2026-08-15，两份真实 PDF 逐页对比）

对照原文 PDF 与 Tessera 解析 blocks + 切块 chunks，逐页逐行检查。

### 检查对象
- 《信息与文献：参考文献著录规则》：扫描件 26 页，615 blocks / 31378 字符 / 227 chunks（OCR 解析）
- 《CCF 推荐国际学术会议和期刊目录-2022》：数字版 73 页，167 blocks / 130378 字符（layout 模型 + OCR，未切块）

### P0：切块内文本重复 + 相邻 chunk 异常重叠
- 现象：19 个 chunk 内相同行重复出现（如"周易外传：卷5..."出现两次）；51/227 个相邻 chunk 有内容重叠（22%），部分是整段重叠而非滑动窗口 overlap
- 影响：RAG 检索召回重复内容，浪费 token 和 embedding 预算
- 方向：查 chunker 聚合 blocks 的合并逻辑，blocks 拼接时去重；检查 semantic chunker 的 overlap 实现是否异常放大

### P0：table_data 的 headers/rows 为空
- 现象：CCF 72 个表格 block + 参考文献 2 个表格 block，markdown 文本完整正确，但 `table_data.headers=[]`、`table_data.rows=0`；chunks 中 table_data 也为空
- 影响：表格无法按行/列结构化检索，只能当纯文本搜
- 方向：查 `_extract_table_data`（或 docling table 导出逻辑），docling 的 `TableData` 对象解析有问题；markdown 是从 `export_to_markdown` 拿的，但结构化行数据没正确提取

### P1：标题层级全是 level 1
- 现象：参考文献 96 个 heading、CCF 48 个 heading，全部 level=1
- 影响：面包屑/结构检索失效，切分粒度不对（"4.1.1 xxx"和"4 著录项目"同级）
- 方向：查 `infer_headings` 逻辑，应按编号层级（1 → 1.1 → 1.1.1）和字号大小区分多级标题

### P1：扫描件目录页 OCR 丢失
- 现象：参考文献第 2-3 页是目录页，原文有 30+ 条目（点状引导线），RapidOCR 只识别出"目 次"标题，条目全丢
- 方向：目录页字号小+点状引导线干扰 OCR；可调高 OCR DPI/images_scale，或对低文本页做二次 OCR（更高分辨率）；长期靠 VLM 兜底

### P1：彩色文字碎裂/丢失（CCF 封面）
- 现象：CCF 第 1 页三行图例，"标黄"和"删掉"两行完全丢失，"红字"行被碎裂成 5 个独立 paragraph 块
- 原因：彩色背景高亮文字（黄/红/蓝）被 docling layout 模型拆分或漏识别
- 方向：查 docling 是否有保留彩色 span 的配置；或解析后用 PyMuPDF 读文本层补充（阶段二 PyMuPDF 预检测可覆盖）

### P2：多语言 OCR（韩文/日文乱码）
- 现象：参考文献第 12 页韩文示例被识别成"- [1]0]号.丘对：1[M].对号：早早"
- 原因：RapidOCR 配置 `lang=["chinese"]`，不支持韩文/日文
- 方向：RapidOCR 支持多语言模型，可按文档 lang 检测结果配置；或中英文档中的少量 CJK 乱码靠 LLM 清洗修复

### P2：超短 block 碎裂
- 现象：参考文献 93 个 block <10 字符（编号"3.2"、"1"独立成块），导致 26 个 chunk <50 字符
- 方向：chunker 合并相邻短块到上下文；或解析后处理把孤立编号合并到后续段落

### P2：图片块 text 为空
- 现象：参考文献 4 个、CCF 18 个图片块 text 为空（CCF 的是每页左侧小 logo/icon）
- 方向：阶段四 VLM 生成描述；CCF 的装饰性小图标可考虑按尺寸过滤

## 外部调研：Tessera项目分析与RAGflow对照（2026-08-13）

> 完整调研见 `D:\1RAG\Tessera项目分析与RAGflow对照.md`（代码通读 + RAGflow main 分支逐文件核实）。
> 以下为 PROGRESS 此前未覆盖的增量，尚未列入待办，动工前先看该文档。

### 代码通读发现的工程问题（未修，按严重度）
1. **切换 Embedding 模型不强制重建向量（严重）**：`runtime.py:78-92` 只换 embedder 单例，无维度校验/清库，新旧维度向量混入同一 LanceDB 表致 KNN 报错或垃圾结果。PRD P0-6 未落地。
2. **LanceDB upsert 是 append 假更新**：`lancedb_store.py:138-150` 表存在走 `tbl.add`；sqlite-vec 才是 `INSERT OR REPLACE`。store_failed 重试只重跑 run_embed，LanceDB 下会追加重复向量。
3. **检索同步阻塞事件循环**：`api/search.py:101` 直接调同步 `retriever.search()`（含 query 编码），未用 `asyncio.to_thread`。
4. **后台任务无队列/无并发上限/无去重**：`tasks.py:73` 裸 `asyncio.create_task`，多文档并发可能抢 BGE-M3 单例 + 6GB 显存 OOM。
5. **缓存 LRU 每次写入 O(n)**：`cache.py:229` `_enforce_lru` 每次 set 都 glob 全部目录 + stat，万级缓存写入变慢。
6. **LanceDB filter 字符串拼接（注入面）**：`lancedb_store.py:107-117` 直接 f-string 拼 where；sqlite-vec 用参数化 `?`，不一致。
7. **父块展开每请求新建 sqlite 连接**：`api/search.py:31` 无连接复用。

### RAGflow 对照与"补能力优先"路线（按召回收益/成本排序）
- (1) 下全 Docling 模型包（TableFormer ~213MB + RapidOCR ~100MB + 图片分类/公式，总 ~1GB，固定经 hf-mirror，设置页点按钮）——零代码，下完即 PDF 表格+扫描件全通
- (2) chunk ID 改内容哈希 + LanceDB upsert 走替换（解重复 + store_failed 重试安全）
- (3) embedding 切换加抽样校验 + 显式全量重建任务
- (4) 图片 OCR + VLM 三级降级填 picture block.text（最大召回增量）
- (5) 表格结构化重建 + 双表示（HTML 存元数据 + 文本融入正文 embedding，优于 preserve/markdown 二选一）
- (6) rerank 分数归一化到 [0,1] + 融合权重可配 + 试 weighted_sum 对比 RRF
- (7) Excel/CSV 解析 + 每行一个 chunk（openpyxl，初版不做列角色）

> 关键判断：Tessera 用 Docling 已与 RAGflow 的 DeepDOC 同代能力，PDF 八阶段切分领先 naive.py，**不需要移植 DeepDOC**；需补的是 Docling 模型包 + VLM 图片描述。

## 已完成

### 后端（158 测试全绿）

- 八阶段切分引擎（结构/语义/LLM/递归 + Parent-Child + overlap + 小块合并）
- Docling 解析封装（加密 PDF / OCR 扫描件重试 / 纯文本直通 / HTML 降级）
- 向量化：LocalEmbedder（BGE-M3 稠密+稀疏）/ APIEmbedder（OpenAI 兼容）
- 四档向量存储：sqlite-vec / LanceDB（默认）/ Qdrant / pgvector
- 混合检索：稠密+稀疏 RRF(k=60) + Rerank（transformers 原生分批推理）+ 父块展开
- 解析质量检测（4 类警告）+ LLM 清洗后处理（块 1:1 映射）
- 解析版本双轨：raw/cleaned 缓存并存，切换后自动重切
- 评估系统：QA 集管理、实验对比、recall@k / MRR
- 导出：JSONL / Alpaca / Dify
- 三级缓存（解析/切分/embedding）+ LRU 淘汰
- REST API + WebSocket（三频道：task_progress/model_download/experiment_progress）
- 工作流改造：解析后停在预览页等验收，手动触发切分
- table_handling 三模式：preserve/markdown/skip

### 前端（6 页，tsc 通过）

- 文档管理页（上传/列表/批量/密码弹窗）
- 解析预览页（块列表/质量报告/LLM 清洗/版本切换）
- 切分预览页（左右联动 block↔chunk 映射/参数面板/章节树/统计面板）
- 检索测试页、评估页、设置页
- UI 组件库 + LLM 配置弹窗 + WebSocket 自动重连

## PM/UX 走查（2026-09-07，产品视角只评不改）

> 方式：起前后端（8766/5173），Playwright 逐页截图走查 6 页 + 关键弹窗（参数/清洗/导出），
> 真实库 4 文档（2 indexed / 2 chunked）+ 3 组真实检索 + SQLite 只读统计。控制台零报错、请求零 4xx/5xx。

### A. 产品级问题（按用户可感知严重度）

1. **【最高】空图片块入库，直接污染检索结果（实测坐实）**
   - 「参考文献著录格式」检索 top5 全是 content="[图片]" 的 image 空块；CCF 库 top10 中 4 条空图片块
   - DB 统计：27 个 image 块全部 ≤10 字符（CCF 18 / DOCX 5 / 参考文献 4），其中 23 个已 indexed 向量化
   - 机制：chunker.merge_small 只对 TEXT 块做「空块丢弃 + 小块合并」，image 块 content 是占位符「[图片]」（非空但零信息），既不丢也不并，一路流向入库；reranker 对其打相同超低分但只重排不过滤
   - 用户感知：RAG 工具核心承诺是「检索得到有用内容」，首屏空白结果直接击穿信任；VLM 描述（阶段四）落地前应有产品级兜底（空信息块默认不入库 / 检索过滤 / 明确开关，三选一，遵循「让用户勾选」原则给开关）
2. **min_chunk_size 参数承诺与结果不一致（信任问题）**：参数面板设 50，列表仍有 1/3/4 tok 块（image 与孤立 text），用户调参后看到参数「没生效」；需在 UI 说明该参数仅作用于 text 块，或让过滤规则可见
3. **P1 标题层级修复（1db9950）对用户不可见——缓存缺口的活体演示**：代码已修，但旧解析缓存不失效，走查时参考文献 PDF 仍全 H1（A.2/B.1 与一级标题同级），章节树 96 个平级条目、chunk 面包屑大面积「（无章节）」，Parent-Child/章节过滤/面包屑三条卖点同时打折。已知 5 处缓存失效缺口中最高优先的「重解析不清切分/解析缓存」应提前修，否则后续每次质量修复用户都看不到
4. **检索页缺「文档范围筛选器」**：PRD P0-8 要求按文档/类型过滤，后端 API 已支持 document_ids/chunk_types，前端 SearchPage 无入口；多文档库检索无法限定范围（痛点盘点早已记录）
5. **Rerank 体验**：① 设置 top_n=5，PRD P0-9 写明只 rerank top20，配置与需求不一致；top10 检索时前 5 精排、后 5 未精排拼接，用户不可见。② 首次检索实测 8.3s（含模型加载），PRD 非功能要求 rerank <2s；检索中只有全局 loading，无「召回完成→精排中」分阶段反馈
6. **质量报告覆盖面与已知问题错位**：4 类警告（no_heading/short_blocks/repeated_lines/garbled_text）检测不到本次最伤的空图片块、标题层级错、目录页 OCR 丢失——参考文献 PDF 不弹警告横幅，用户以为质量合格。质量守门人没守住已知坑

### B. 信息呈现/易用性问题（不阻塞但影响作品集第一印象）

- 工程内部信息泄漏到用户界面：图片块显示「bbox [397,811,515,750]」坐标；统计直方图标题「v5·灰色为 v4 对照」（内部版本计数，首屏无上一版仍显示「-7 vs 上一版」差值）；chunk 卡片「（无章节）」「结构/语义/父子」标签无解释
- 文档管理页 12 个状态 chip 平铺（已上传/解析中/…/写库失败）= 内部状态机直接外露，建议按「进行中/完成/失败」折叠分组
- 文档行操作默认 opacity-0 悬停才出现且只有图标（触屏无 hover、可发现性差）；chunked 文档行内无「直接入库」入口，必须进切分预览页，事后补入库动线偏长
- 术语不统一：Embedding 页叫「连通性测试」、Rerank 页叫「加载测试」；参数面板中英文混用（chunk_size/overlap 与父块大小并存）
- 解析/切分预览作为流程中间页平铺在一级导航，无文档时自动落第一个文档，缺少「从哪来」的上下文；可接受但建议加文档步骤指示
- 检索稀疏通道：中文查询 sparse_rank 仍大面积 None（MEMORY 已挂待办「稀疏通道未闭环」），混合检索卖点需补真实集成验证

### C. 做得好的（保持）
- 整体视觉统一（浅色卡片/圆角/状态色），LLM 清洗弹窗三档 + 表格增强独立开关 + token 预估，符合「选项交给用户」原则
- 导出 JSONL 双模板 + 章节过滤完整；设置页模型「已下载/默认」标签、下载源说明、失败降级文案到位
- 空状态普遍有引导文案；删除有级联提示；前端驼峰/后端 snake 边界层转换正确（use_rerank/top_k 实测正常，非 bug）

### D. PM 建议优先级
1. 空信息块入库策略（产品决策 + 最小拦截）→ 2. 缓存失效缺口（让已修的质量问题真正被用户看到）**【已完成 09-07 晚】** → 3. 重解析现有 4 个文档验证标题层级效果 → 4. 检索范围筛选器 + Rerank top_n/分阶段反馈 → 5. B 类界面信息卫生批量清理
