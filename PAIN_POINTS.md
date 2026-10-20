# Tessera 痛点解决记录

记录开发过程中遇到的真实 bug / 硬卡点：现象、根因、解决、验证、状态。
按时间倒序排列。每条尽量附可复现的关键信息（文件、命令、数据），方便日后回查。

---

## 2026-10-08 稀疏检索噪声：逐字分词 + BM25 长度归一化，正确答案系统性输给"文字墙"

- **状态**：已修复（commit `c594c31`），251 pytest 全绿 + 评测集正例 hit@5 = 12/12
- **涉及文件**：`backend/app/core/vector_store.py`（`fts_tokenize`）

### 现象
- 3 组不相关查询的检索 top5 恒为 dense/sparse 交错，第 2、4 位永远是 Planning.txt 的纯稀疏命中（RRF 分 `1/61` 与 dense#1 并列第一）
- 深挖后比"霸榜"更严重：查「参考文献著录格式」，正确答案文档（著录规则 PDF，36 块含该短语、索引在位——单字查「献」能命中它 8/10）**连 FTS top15 都进不去**；查「刊物全称」CCF 目录零命中
- 单字「献」的 top1 是 Planning 一个 318 字符块（1.66 分），高于著录文档的长块

### 根因（两层叠加）
1. **逐字分词 + OR 语义**：`fts_tokenize` 把 CJK 逐字拆（`参考文献著录格式` → `参 考 文 献…`），任何含任一常用字（与/的/是/学）的长文本都命中——实测查「图像裁剪与镜像变换」，只命中一个「与」字的块（score 1.63）也进 FTS top3；对照「龘靐齉爩麤」返回 0 条，证明确实在按字过滤
2. **BM25 长度归一化**：著录文档表格密集、块全库最长 → 长度惩罚最重；Planning 中等长度文字墙每字蹭分加总反超。**正确答案块的稀疏分系统性低于噪声 → 任何稀疏侧阈值都无法"滤掉噪声、保留正确答案"**（这是"稀疏阈值"方案被实测数据否决、只留 rerank 阈值的原因）

### 解决
- CJK 连续段切**相邻二元**（`参考文献` → `参考/考文/文献`），段长 1 退单字；非 CJK 段逐字原样输出、交给底层 unicode61/FTS5 分词器——**不能拼接**，否则 `FastAPI` 被拆成 `F a s t A P I`
- `FTS_TOKENIZER_VERSION=2` + 8 个 golden 用例钉住（含「图像API中文」段边界）；LanceDB 与 sqlite-vec 共用本函数
- 迁移：重入库（upsert 按 content 重算影子列），无需一次性脚本
- 负例噪声由 rerank 阈值收口：正例 top1 精排分 `0.99` vs 负例 `0.010`，差百倍

### 验证
- 真实库重切重入库后：「刊物全称」FTS 命中 CCF **10/10**（修复前 0）；「参考文献著录格式」top5 全为著录文档，Planning 退出 top5
- 评测集 `docs/retrieval_eval.json`：正例 hit@5 = 12/12（11 条 rank=1）
- raw FTS 通道 top3 仍是 Planning（长度归一化未动），但融合层已正确——后续要动 raw 侧时通道分数（dense_score/sparse_score）已可观测

### 教训
- 混合检索的稀疏通道不能只看"能不能命中"，要审**分词粒度 × 打分归一化**的交互：粒度越细 OR 命中越滥，长块在 BM25 里越吃亏，两者叠加会把正确答案系统性挤出去
- 阈值类方案先测分数分布再定：正确答案分数系统性低于噪声时，阈值无效（本例实测推翻了"稀疏阈值优先"的推荐）
- 检索验证必须配"有答案 + 无答案"两类查询（之前拿全库无答案查询验证，结论被污染）

---

## 2026-10-08 CCF 残留 9 个超短块：归因三次反转，终版为 image/heading 打断聚合（image 路径已修）

- **状态**：image 打断路径**已修复**（commit `1371b42`，2026-10-10）——空图片块丢弃提前到
  `normalize_blocks`（聚合之前），幽灵打断源头消除，真实 CCF 重切验证孤立标签块 6→0。
  heading 打断路径未修：残留 1 个超短 unit（「（计算机科学理论）」，标签在 section 末尾、
  下一 heading 开新 section）——评测无可测伤害、标签语义上是真实相关命中、rerank 阈值
  生效后大概率被滤，保留 P3 优先级
- **涉及文件**：`backend/app/core/structure.py`（`normalize_blocks` / `aggregate_section`）、
  `backend/app/core/chunker.py`（`_merge_tiny_pieces` / `merge_small`）

### 现象
C 方案（`_merge_tiny_pieces`，碎片层小前导段并入下一碎片）落地重切后 423→419 块，但库里仍有 9 个 ≤14 字符的 text 块：6 个 CCF 分区标签（「（网络与信息安全）」等）+ 3 个著录 PDF 抽取残渣（「GB」「1993(7):50-58.」等，另一族问题与 chunker 无关）。

### 归因三次反转（方法论教训比结论更值钱）
1. **v1「section 边界切开」**：被「（计算机网络）」块 block_ids=#34-37（含 3 表格）推翻——碎片继承单位级 block_ids，不能反推内容来源
2. **v2「段落级拆分甩出碎片」**（C 方案依据，已修该路径）：`_split_text_unit` 按 \n\n 拆分时 7 token 标签放不下表格段被 `flush_buf()` 单独吐出
3. **v3 终版「image/heading 打断聚合，独立 unit」**：决定性判据是 `len(block_ids)==1`（碎片必继承全 unit 的 ids ≥2）。6 个标签块全部 ids=1 → 聚合层就独立成 unit，C 方案（碎片层）与 merge_small（前邻 bc 约束）都够不到
- **每版都被下一版的单样本证据推翻**。教训：推翻归因时新证据必须覆盖旧证据全部样本（6 块逐一核销），否则只是换一种以偏概全

### 终版机制（缓存 block 级对账 6/6 闭合）
- **5/6 被 image 打断**：文档流为 `heading(推荐目录) → paragraph(分区标签) → image → table`。`aggregate_section` 遇 image 触发 `flush_text()`，标签单独成 unit；image 块随后被 drop_empty_blocks（空信息过滤）丢弃——**两道工序时序造成"幽灵打断"**：打断者自己消失了，只剩孤立标签 unit。**该路径已修**：`normalize_blocks` 在聚合前就丢弃空图片块（随 drop_empty_blocks 开关），文本流不再被打断（CHUNKER_VERSION=empty-img-early-1）
- **1/6 被 heading 打断**（idx=29「（计算机科学理论）」）：标签后紧跟「二、B类」子标题，表格进了子 section——**未修**，是修复后唯一残留（1 个超短 unit）
- 对照组：标签直连表格的分区（如「（计算机网络）」）标签+表格聚成同一 unit → 超限拆分甩出碎片 → C 方案并回（修复生效形态）
- 表格 markdown 模式**不**打断聚合（`text_buf.append`，structure.py:183-186）

### heading 打断路径的修复方向（未实施，按上游程度排序）
1. 小标签并入下一 section 的首个 unit（需判定"孤立小段落"启发式）
2. unit 层超短合并兜底（不依赖归因，但治标）

### 排查过程踩的坑（都已固化进 MEMORY）
- **父子 chunk_index 编号空间重叠**：探针按 chunk_index 查"前后邻块"没按 role 分流，两轮结论被污染而不自知。实锤标志：邻块 idx 与已知块 idx 相同但内容对不上
- `len(block_ids)` 是「独立 unit vs 拆分碎片」的决定性判据

---

## 2026-08-15 PDF 多页解析漏字（std::bad_alloc）

- **状态**：已修复（commit `fd304f6`），183 pytest 全绿 + 两份真实 PDF 验证
- **涉及文件**：`backend/app/core/parser.py`

### 现象
用户上传两份 PDF（参考文献著录规则 17MB/26 页、CCF 目录 73 页），解析结果大量漏字：
- CCF 目录 73 页：默认配置只成功 18-40 页，35-55 个 bad_alloc 错误
- 参考文献著录规则：旧缓存仅 23 blocks/2429 字符（11 页），实际 26 页/56433 字符

### 根因
docling-parse（Rust 后端）预处理阶段**一次性渲染所有页面**，多页 PDF 内存累积导致
`std::bad_alloc`，后续页面**静默失败**。错误记录在 `ConversionResult.errors` 里，
但 Tessera 之前从未检查该字段，所以表现为"漏字"而非报错。

### 解决
1. **PDF 分批转换**：新增 `_parse_pdf` / `_convert_pdf_batches`，用 pypdfium2 获取总页数，
   每批 10 页转换后合并 blocks，避免内存累积。73 页 PDF 实测 0 bad_alloc / 51s /
   133516 字符全页覆盖。
2. **显式 PdfPipelineOptions**：`_build_converter` 始终传 PdfPipelineOptions（之前不传，
   docling 默认 do_ocr 行为不确定）；数字版 do_ocr=False，扫描件 do_ocr=True +
   RapidOcrOptions(lang=["chinese"], backend="onnxruntime") + images_scale=2.0。
3. **转换错误检查**：分批转换后检查 `result.errors` 并记录日志（不再静默）。
4. **Docling 路径元数据补充**：`parse_with_meta` 中 infer_headings 后调
   `extract_metadata(blocks, docling_meta=meta)`，修复 PDF/DOCX/PPTX 无内嵌元数据时
   title/lang 全 None 的问题。

### 验证
- 183 pytest 全绿
- 参考文献著录规则：615 blocks / 56433 字符 / 26 页（完整）
- CCF 目录：167 blocks / 133516 字符 / 73 页（完整，0 bad_alloc）

### 遗留边界
- 页数获取失败（pypdfium2 异常）时回退 `_parse_pdf_legacy` 一次性转换（旧行为），
  该降级路径理论上仍可能 bad_alloc，但 pypdfium2 获取页数几乎不会失败，属可接受兜底。

---

## 2026-08-14 Docling 模型下载三连坑（CAS 401 / RapidOCR 路径 / 后台进程被清理）

- **状态**：已解决（08-14），tableformer + RapidOCR 就绪，扫描件 PDF 端到端验证通过
- **涉及文件**：`backend/app/services/model_manager.py`、`backend/app/main.py`
- **详细方法**：见 `MODEL_DOWNLOAD.md`（源目录结构 / 精确下载 / 后台任务模板）

### 坑 1：tableformer 下载命中 CAS 401
**现象**：`download_models()` 下载 docling 大模型抛
`CAS Client Error: HTTP status client error (401 Unauthorized), domain: cas-server.xethub.hf.co`。

**根因**：新版 docling 大模型（layout onnx 变体、tableformer）已迁移到 Hugging Face 的
**xethub 存储**，hf-mirror 镜像对该 CAS 服务返回 401。

**解决**：设 `HF_HUB_DISABLE_XET=1` 禁用 Xet 协议，回落标准 HTTP 下载（实测有效）。
已固化到 `model_manager.py::download_docling_bundle()`（含环境变量恢复）与 `main.py` 启动时。
注意 tableformer 的 revision 是 `v2.3.0`（非 main），路径 `model_artifacts/tableformer/{accurate|fast}/`
（accurate 203MB + fast 139MB）。

### 坑 2：RapidOCR 路径不匹配（非下载，是复制）
**现象**：RapidOCR 模型文件实际已随包安装在 site-packages（约 31MB），但 docling 设了
`DOCLING_ARTIFACTS_PATH` 后期望在 `docling/RapidOcr/` 目录找，路径不匹配导致 OCR 失败。

**解决**：用 docling 内部解析逻辑（`_resolve_rapidocr` / `_rapidocr_artifacts` /
`_backend_to_engine_type`）确定期望文件，把 rapidocr 包内 3 个 onnx 复制到
`docling/RapidOcr/`；补装 `onnxruntime`。验证：`RapidOcrModel init OK, reader ready: True`。

### 坑 3：后台下载进程被 WorkBuddy 周期性清理
**现象**：Start-Process 起的 python 下载进程会被 WorkBuddy 外部机制周期性清理，长任务中断。

**解决**：用 `schtasks /create + /run` 一次性任务独立运行，完成后删除任务。

### 验证
- 扫描件 PDF 端到端：RapidOCR 3 个 onnx 从 docling/RapidOcr/ 加载，43 blocks / 2077 字符，
  识别出标题，耗时 17.1s
- 后端 pytest 全绿确认无回归
