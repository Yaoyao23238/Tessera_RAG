# Tessera 开发进度

更新时间：2026-10-10

## 当前状态：P0 全链路跑通 + 工作流改造完成 + 痛点盘点与阶段规划 + 本地 LLM 方案确定 + 阶段一解析质量加固完成 + PDF 分批解析修复 + 解析/切块质量检查（8 个问题）→ **两个 P0 + P1 标题层级均已修复 + 5 处缓存失效缺口全部修复（2e3b242）** → **空信息块过滤开关 + 存量 4 文档重解析/重切/重入库 + config_hash 漏字段与设置合并两处同族缺口（bd416d6 / c783221）** → **精排最低分阈值 + FTS 索引假设钉死（073813d）** → **CJK 二元分词 + 通道分数可观测性 + 检索评测集 + 过小碎片合并（c594c31）** → **P1 双件套：top_n 对齐 PRD + min_score 默认 0.1 + 空态语义区分 + 评测回归门（a53bc13）** → **评测链路 2 个 P1 修复：文档级命中判定 + qaset id 显式生成（指标恢复 R@5=1.0，65 tests 全绿）（c8e1217）** → **产品使用问题 4 项修复：sqlite-vec 影子表 bug + 导出思维链清洗 + 检索事件循环阻塞与冷启动预热 + 检索范围筛选器（2432f4c/30f62e1/29857d3/ae7e61f）**

- 后端 **291 pytest 全绿 + 3 skipped**（评测回归门离线跳过）；前端 **tsc -b 零错误**
- 检索评测回归门（真实库实跑）：正例 **12/12 hit@5**、负例 **6/6 全空**；延迟 中位 223ms / p95 391ms
- 最新提交：
  - `ae7e61f` feat: 检索页文档/内容类型筛选器（PRD P0-8）
  - `29857d3` fix: 检索移出事件循环 + 启动预热模型权重（冷启动 45.9s → 首查 1.3s）
  - `30f62e1` feat: 导出可剥离 AI 对话存档思维链（strip_cot）
  - `2432f4c` test: sqlite-vec FTS5 真实集成测试 + 影子表 upsert 修复
  - `c8e1217` fix: hit_rank 文档级命中判定 + qaset id 显式生成
  - `a53bc13` feat: min_score 默认 0.1 + top_n 对齐 PRD + 空态语义区分 + 评测回归门
  - `c594c31` feat: CJK 二元分词 + 通道分数可观测性 + 检索评测集 + 过小碎片合并
  - `073813d` feat: 精排最低分阈值设置项 + 钉住 FTS 索引新鲜度假设
  - `c783221` fix: config_hash 补齐父子块大小 + 设置分组改深度合并
  - `bd416d6` feat: 空信息块过滤开关（图片/表格零文本不入库）
  - `8e9a42c` docs: 更新 PROGRESS 提交记录（2e3b242 / 1db9950）
  - `2e3b242` fix: 修复 5 处缓存失效缺口（P1）
  - `1db9950` fix: 标题层级细化（P1）
  - `e9c7ea7` fix: table_data 结构化提取
  - `a9ac089` fix: overlap前缀U+FFFD污染修复 + 父块拼接剥前缀 + 预览页右栏排序与联动单向
  - `824ec2d` fix: 切块重复与相邻整段重叠修复（P0）
- 端口固定 **8766**（后端）/ **5173**（前端），vite 代理已同步

## 今日进展（10-10）：产品使用问题 4 项修复（审查报告验证 → 修复 → 实测）

> 背景：审查报告 20 条逐条实证后筛出 5 条影响产品使用，本日修复其中 4 项
> （第 5 条「P1-A/P1-B 未提交」以 c8e1217 固化）。原则：不修一个 bug 引出新 bug。
> 基线 259 passed+3 skipped → **291 passed+3 skipped**、tsc 零错误。

### 一、sqlite-vec FTS5 集成测试 + 影子表 upsert 真 bug（2432f4c）
- 新增 `tests/test_sqlite_vec_store.py`（18 用例）：upsert 幂等 / 删除 / dense 排序与过滤
  （vec0 与暴力余弦双路径）/ sparse bigram / 单字预期不命中 / 重启后 FTS 可见性，
  对齐 LanceDB 侧语义——四档向量库中 sqlite-vec 此前零测试
- **测试暴露真 bug 并修复**：FTS5 影子表的 `id UNINDEXED` 无唯一约束，`INSERT OR REPLACE`
  永远只追加——同 id 更新后旧内容仍被稀疏检索命中、同 id 重复行。改为先 DELETE 后
  INSERT（与 delete_by_document 同写法）。2 红 → 34 绿

### 二、导出思维链清洗 strip_cot（30f62e1）
- 事实坐实：「【DeepSeek 推理过程】」是 **Planning.txt 原文自带**（AI 问答存档），解析 1:1
  保留，导出零清洗 → 思维链混进 alpaca/dify 训练数据
- `exporter.strip_cot_from_chunks`：状态机剥离「【xx 推理过程】…【xx 回复】」区间；
  保守原则——孤立回复标记不动、无标记文档逐字节不变、仅剩空白的块整条剔除；
  分隔行允许与标记**隔空行**（真实文档结构，离线探针坐实 ±1 紧邻规则删不掉）
- **文档级信号放宽孤立回复消费**：切分 overlap 前缀让下一 chunk 重复已消费标记，
  不消费则残留整段思维链；文档未出现起始标记时行为不变
- `exporter.document_flow_order`：按原文文档流重建顺序（chunk_index 顶层/子块两套编号
  空间混排会交错，乱序会让状态机误删正文——与前端 sortByDocumentFlow 同款问题）
- 前端 ExportDialog「内容处理」开关（**默认开**，可取消）+「已对 N 个块去除思维链」
- 测试 +12；真实 Planning.txt 双口径验证：离线探针 50 块思维链 4→0 残留 0；线上 API
  strip=true 26 行（21 块清洗）vs strip=false 46 行原样

### 三、检索事件循环阻塞 + 冷启动预热（29857d3）
- **隐藏 bug**：`api/search.py` 同步调 retriever.search 未包 to_thread——45.9s 冷加载
  卡死整个事件循环（WS/health 一并无响应）。已移入 asyncio.to_thread
- lifespan 后台预热（embedder + 已下载的 reranker）：首查 45.9s → **1.3s**（实测）；
  **只预热已下载模型**——踩坑：预热直接 ensure_loaded 会在测试/未下载环境触发 GB 级
  下载拖死 pytest（已修 + 关停 cancel 任务防悬挂线程）
- embedder/reranker `_load` 加 threading.Lock 双检（预热与首查并发只载一次）；
  `/api/health` 加 `models: {embedder_ready, reranker_ready}`（只读已构建实例，零副作用）
- 实测：启动后 health 110ms 可响应（预热中）→ 45s 后双 true → 首查 1298ms / 热态 230ms

### 四、检索范围筛选器 PRD P0-8（ae7e61f）
- `SearchFilters` 组件：文档多选（仅列已入库）+ 内容类型（正文/表格/代码/图片/标题），
  「筛选 N」/清空交互；SearchPage 请求带 documentIds/chunkTypes（空 = 不过滤）
- 空态新增「当前筛选无结果」分支（有筛选时优先于阈值/无召回提示）
- API 实测：document_ids=[Planning] 查「参考文献著录格式」→ 0 条；chunk_types=[image] →
  0 条；正向著录 PDF → 10 条。浏览器走查 6 步全过，控制台零报错

### 五、验证与遗留
- 全量 `291 passed, 3 skipped`（15.6s）+ `tsc -b` 零错误；5 个 commit：c8e1217 →
  2432f4c → 30f62e1 → 29857d3 → ae7e61f
- 顺带修正：reranker.py docstring「默认 0 = 不过滤」与代码（0.1）不一致
- 观察项（未动）：ExportDialog 关闭后勾选态不复位（记忆上次选择，属交互偏好）；
  「召回完成→精排中」分阶段反馈（需改 API 契约，单独立项）；导出为多文档时行序已按
  文档流修正（对训练数据无害，属顺带改进）

### 六、后续计划清单批次（10-10 晚，按 ROI 顺序执行）
- **评测残留已清**：exp_20261009_031222（P1-A 证据）连同 exp_485caed4 向量表删除，
  仅存 exp_doc_level_fix；retrieval_eval.json 测试集保留
- **审查遗留小项（3e4439e）**：检索页补可见「检索」按钮（触屏）；upload_qaset 的
  name 改 multipart 表单字段 + `_parse_qa_file` 用 utf-8-sig 剥 BOM（+回归测试）；
  设置页向量库面板标注验证状态（LanceDB/sqlite-vec 已测 vs Qdrant/pgvector 未联调，
  选中显示警示条）
- **幽灵打断修复（1371b42）**：空图片块丢弃提前到 normalize_blocks（聚合前），
  flag 关闭时行为不变；CHUNKER_VERSION → empty-img-early-1（旧切分缓存失效重切）。
  真实 CCF blocks 离线验证：孤立标签块 **6 → 0**；残留 1 个超短 unit 是 heading
  打断场景（section 边界另一根因），不在本次范围
- **CI workflow（348702e）**：`.github/workflows/ci.yml` 后端 pytest + 前端 tsc
  （ubuntu-latest / Py3.13 / Node20；无 package-lock.json 缓存键用 package.json）。
  **推送后才会真正跑**；pytest-cov 需新增 dev 依赖，待用户批准后加
- **待拍板（git 保命，卡外部依赖）**：gh CLI 未装、无 remote。需定：① 仓库名
  （建议 Tessera）② 公开/私有（开源作品集→建议公开）③ 建仓方式（装 gh CLI /
  GitHub connector / 用户网页建仓给 URL）。.gitignore 已完备（data/、*.db、
  node_modules 已排除）

### 七、文档体系整理（10-10 深夜，根源改写原则）
- **规则沉淀（AGENTS.md）**：§6 升级为「修改事实性问题从根源改对，不打补丁」（禁止补丁堆/声明掩盖过时/只标注不改错）；§9 新增「最小实现优先（Ponytail 精神）」——Ponytail 是 GitHub 热门 AI 编程插件（写码前先想更轻量实现），经评估不装插件、精神收进铁律
- **PROGRESS.md 拆分**：831 → 308 行（活内容：当前状态/近期进展/待办/规划/环境），12 个历史节移入 `docs/progress-archive.md`（537 行），新接手 20 行看清现状
- **ARCHITECTURE.md 逐节校对改写**（13 个章节）：技术栈/架构图/交互流程/状态机/数据模型/切分八阶段/向量化/检索/存储/缓存/错误处理/API 概览/工程约定/MVP——全部过时断言（Chroma/NumpyStore、缺 rerank 阈值、缺 CHUNKER_VERSION、config_hash 清单缺项、WS 路由等）直接改对到与代码一致，未用声明掩盖
- **PAIN_POINTS.md 事实改对**：幽灵打断条目状态从「P3 未修」更新为 image 路径已修（1371b42）+ heading 路径唯一残留
- **README.md 重写**：定位/徽章/3 张实测截图（docs/images/）/核心能力/快速开始/文档导航表/已知边界；**LICENSE（MIT）新增**（署名 Yaoyao23238，如需改协议/署名告诉我）
- 归属裁定：bug 归因以 PAIN_POINTS 为唯一事实源，PROGRESS 指向它；历史进展以 progress-archive 为唯一事实源

### 八、推送到 GitHub（10-20，仓库 Tessera_RAG）

- 仓库创建 + 推送完成：`https://github.com/Yaoyao23238/Tessera_RAG`（**Private**，先保命后转公开）
- 38 个 commit 全量上去，`main` = `e8edb7f` 与本地一致；分支名 master → main（GitHub 惯例，CI 触发分支含 main）
- 推送前实测：294 passed + 3 skipped、tsc 零错误、工作区干净
- CI 首跑结果：Run 1/2 红（backend 装依赖 `Errno 28 No space left on device`——CUDA 版
  torch + nvidia-* 全家 + docling 撑爆 runner 磁盘，测试没跑到）→ 修复 `af1682e`（workflow 清
  runner 预装大件 + CPU 版 torch 占位，不动依赖清单）→ **Run 3 全绿**（backend+frontend）
- **已转 public**（10-20，用户授权后 Computer Use 操作：Danger Zone → Change visibility →
  Make public，双重确认含影响告知页；仓库徽标实测 Public）；CI #4/#5 均绿
- 建仓方式：Computer Use 操作 Edge 完成（名字 Tessera_RAG / Private / 不勾 README·gitignore·license）；
  git push 凭据管理器静默通过，无需 gh CLI。**网络注意**：本机直连 GitHub 已不通（curl=000），
  push 需走 v2rayN socks5（127.0.0.1:10808），单次 `git -c http.proxy=socks5://127.0.0.1:10808 push`
  （不持久化 git 配置）

### 九、隐私清除与历史重建（10-20 晚）
- **背景**：仓库公开后自查发现个人信息残留（真实姓名/学号/个人画像查询/打码前截图/QQ 邮箱提交身份），用户拍板彻底清除、历史只留最新
- **文本脱敏**（947745c）：测试夹具假名化、评测查询泛化、归档文档文件名匿——测试语义未破坏（38 passed）
- **截图替换**：切分预览图重截为公开国标（GB/T 7714）文档，playwright 无头脚本自带隐私断言；不再需要打码
- **历史重建**（a694760）：orphan 单快照提交 + force push 覆盖远程——远程只剩 1 个 root commit，含隐私的旧历史全部消失；提交身份改 GitHub noreply（原身份邮箱含 QQ 号）
- **全库复扫零残留**（姓名/学号/手机号/QQ/个人画像词）
- 教训：①截图/测试夹具/提交邮箱都是隐私载体，公开发布前要按"身份信息三件套（姓名/学号/联系方式）"全面扫 ②git 历史里的旧版本同样可翻，脱敏必须连历史一起处理 ③CI/测试的假数据必须用假名

## 今日进展（10-09）：浏览器逐页 UI 走查完成 + 评测链路 2 个 P1

> 方式：真实前后端（8766/5173）浏览器逐页交互走查 6 页 + 导出弹窗，API 层对照实测。全程控制台零报错、请求零 4xx/5xx。

### 一、走查结果（6 页 + 导出链路）

| 页面 | 结果 |
|---|---|
| 文档管理 | ✅ 4 文档全「已入库」，状态正常 |
| 解析预览 | ✅ 章节树层级正确、表格/图片块正常（页眉重复项为观察项） |
| 切分预览 | ✅ 章节树跳转联动正常、阅读位更新正确（高亮仅悬停驱动属设计） |
| 检索测试 | ✅ 详见下 |
| 质量评测 | ✅ UI 链路全通，但指标计算有 P1（见三） |
| 设置 | ✅ top_n=20 / min_score=0.1 显示正确，阈值说明文案已落地 |
| 导出 JSONL | ✅ 真实链路：POST /api/export/jsonl → 46 行 alpaca 落盘 `data\exports\`，弹窗反馈路径/行数/复制按钮 |

**检索测试页明细**：冷启动首查 **45.9s**（模型加载）→ 热态 **274 / 309ms**；正例 top10 全部来自《参考文献著录规则》（Planning 退出 top10）；卡片渲染齐全（页码 / Rerank / 稠密 / 稀疏 / 通道排名徽标 / breadcrumb / RRF 分）；负例→「无足够相关的结果」+ 28 条候选被门槛滤除 + 调阈值引导；单字「献」→ 同空态（threshold_dropped=10）；空态语义三分设计可见。

### 二、P1-A：评测指标全 0 —— 评测集格式与引擎命中口径不兼容

- 实测：导入 `docs/retrieval_eval.json`（18 条）运行实验 → **R@1/R@5/R@10/MRR 全 0.000**，与检索层正例 hit@5=12/12 的事实矛盾
- 根因：`core/evaluator.py` `hit_rank` 开头 `if not source_chunk_id: return None`——引擎只认 `source_chunk_id`（+可选 `source_content` 内容重叠），而评测集条目只有 `expect_document`（文档名级期望），引擎根本不读该字段 → 全部判不命中
- 附带：`docs/retrieval_eval.json` 的 usage 写「正例带 source_content 供 recall@k 计算」，但文件里 12 条正例并没有带——usage 与文件内容不一致
- **候选修法（需拍板）**：A. 评测集补 `source_chunk_id`/`content` 快照（工作量小但快照随重切过期）；B. `hit_rank` 扩展文档级命中（结果 chunk 的 `source_file == expect_document` 也算命中，与 `test_eval_gate.py` 的 hit@5 口径一致，不随重切过期）——**倾向 B**
- 非检索退化：检索层指标已由评测回归门（a53bc13）独立验证

### 三、P1-B：qaset 文件名恒为 `None.json` —— 多测试集互相覆盖 + 删除连带

- 实测坐实：上传 18 条评测集落盘 `data\qasets\None.json`（3082B）→ 再上传 1 条探针 → **同名文件被覆盖**（探针内容顶掉 18 条数据），DB 两条 qaset 记录 `file_path` 指向同一文件
- 根因：`QASet.id` 是 `default=_uuid`（SQLAlchemy 列默认值，**flush 时才生成**），`api/evaluation.py` 的 `upload_qaset` 在 `session.flush()` **之前**就用 `qaset.id` 拼文件名 → 拼出 `None.json`
- 连带：`delete_qaset` 按 `file_path` 删文件 → 删除任一测试集会删掉 `None.json`，其余测试集数据全部丢失
- 修法：显式生成 id（构造时赋值）或 flush 后再拼路径写文件；一行级修复

### 四、观察项（不阻塞，待排期）

- 检索框只有回车触发、无可见「检索」按钮——触屏用户无键盘难触发（与「文档行操作悬停才出现」同类触屏问题）
- `upload_qaset` 的 `name` 是 query 参数而非 form 字段，前端传法需对照；`_parse_qa_file` 不剥 UTF-8 BOM（PS5.1 默认编码上传会解析失败）
- 导出 content 开头含「【DeepSeek 推理过程】」分隔线与思维链文本——疑为 Planning.txt 原文档自带（AI 回答直接存档），待用户确认是否需要清洗规则
- 走查工具与 shadcn Input 的兼容坑（非产品 bug）：`fill` 不触发 React onChange、`Control+A`/`Backspace` 派发无效、`type` 是追加模式、input 无 `type` 属性导致 `input[type=text]` 选择器失灵

### 五、遗留动作

- 本次走查创建了评测实验 `exp_20261009_031222`（指标全 0，为 P1-A 的证据，修复后需重跑替换）与测试集记录 `retrieval_eval.json`（已恢复 18 条完好数据）；探针已清理
- sqlite-vec FTS5 集成测试补位、Docker Compose 验证（端口 8766）+ stores 层补测试仍未做

### 六、P1-A / P1-B 修复落地（10-09 晚，拍板走修法 B）

**P1-A 修法 B（文档级命中）已实施**：
- `core/evaluator.py` `hit_rank` 扩展文档级命中：结果 chunk 所属文档名 == `expect_document` 也算命中；负例（无 `source_chunk_id` 且无 `expect_document`）不计入 Recall/MRR 分母
- `workers/tasks.py` 收集 `doc_id → filename` 映射传入 `evaluator.run_experiment`
- `api/evaluation.py` 上传时把 `expect_document` 并入 `QAItem.meta`（免加 DB 列，meta 直通 evaluator）
- `docs/retrieval_eval.json` usage 文案同步修正（删 source_content 描述，写明文档级口径）
- 选 B 的理由：补 chunk 快照（修法 A）随重切过期，等于给过期 id 打补丁；文档级口径与评测回归门 hit@5 一致，不随重切失效
- **验证**：新建实验 `exp_doc_level_fix`（c6b86c36）→ **R@1/R@5/R@10/MRR 全 1.0**（正例全命中，负例不进分母）；`pytest tests\test_evaluator.py tests\test_api.py -q` → **65 passed**（含新增文档级命中/负例过滤回归用例）

**P1-B 已修复**：
- `api/evaluation.py` `upload_qaset` 构造 `QASet(id=_uuid())` 显式生成 id（列默认值 flush 时才赋值，原代码 flush 前拼文件名拼出 None.json）
- **验证**：`data\qasets\` 现仅存 UUID 命名文件（f86705ae....json），无 None.json；同上 65 passed

**踩坑**：PS 5.1 `Invoke-RestMethod` 发中文 query 默认 ISO-8859-1 编码 → 乱码；须 `[System.Text.Encoding]::UTF8.GetBytes($json)` + `ContentType "application/json; charset=utf-8"` 显式 UTF-8 发送。

## 08-15 质量检查问题清单（当前任务单）

| 优先级 | 问题 | 状态 |
|---|---|---|
| P0 | 切块重复 + 相邻重叠 | **已修复**（08-17，见上） |
| P0 | table_data.headers/rows 全空 | **已修复**（09-07，见上） |
| P1 | 标题层级全 level 1 | **已修复**（09-07，见下） |
| P1 | 目录页 OCR 丢失 | 待分析 |
| P1 | 彩色文字碎裂 | 待分析 |
| P2 | 多语言 OCR | 待分析 |
| P2 | 超短块碎裂 | 与 P0 重复同源，已大幅缓解（chunks 227→150） |
| P2 | 图片块空 | 待分析（关联本地 LLM 方案图片描述） |


## 本地 LLM 方案（08-14 确定）

**目标**：本地 LLM 用于三个场景——RAG 问答（检索向量库生成回答）、LLM 辅助切分（已有功能）、图片识别（给文档图片块生成描述，解决图片信息全丢）。

**硬件约束**：RTX 4050 Laptop 6GB 显存。

**选定方案：llama.cpp / llama-server + Qwen3-VL-4B GGUF Q4_K_M**

- 模型：Qwen3-VL-4B（多模态，文本+图片），Q4_K_M 量化语言模型（约 2.3GB）+ Q8_0 视觉编码器 mmproj（约 432MB），合计约 2.8GB
- 显存占用约 3GB（含 KV cache），6GB 安全
- llama-server 原生提供 OpenAI 兼容 API（`/v1/chat/completions`），Tessera 设置页直接填地址即可，无需改 LLM 客户端代码
- 独立进程，不与 Tessera 的 BGE-M3/reranker 抢显存
- GGUF 从 ModelScope 下载（`Qwen/Qwen3-VL-4B-Thinking-GGUF`，国内速度快）
- 已有 FP16 模型在 `D:\3AIC\models\Qwen3-VL-4B-Instruct`（8.5GB），6GB 显存无法直接加载，保留备用
- 备选：Ollama（安装包/模型官方源国内下载慢，暂缓）；transformers+bitsandbytes 4bit（需写服务代码、启动慢，备选）

**实施步骤（文档解析弄好后）**：
1. 从 ModelScope 下载 GGUF 文件到 `D:\OllamaModels\gguf\`（modelscope 已装于 .venv）
2. 下载 llama.cpp Windows CUDA 预编译版，解压到 D 盘
3. 启动 llama-server（`--mmproj` 加载视觉编码器，监听如 11434 端口）
4. Tessera 设置页配置 LLM：base_url=`http://127.0.0.1:11434/v1`，model=`qwen3-vl`
5. 验证辅助切分端到端
6. 开发 RAG 问答聊天功能（后端 `/api/chat` 检索+生成+流式，前端第七页聊天界面）
7. 图片描述：解析时对图片块调 VL 模型生成描述存入 block.text，正常向量化
## 痛点盘点与阶段规划

### 痛点核实结论

**真实且高优先级：**
- **PDF 解析硬卡点：tableformer 模型缺失**。docling 解析 PDF 需要 layout-heron（已有）+ tableformer（缺失，约 200MB），缺 tableformer 直接 FileNotFoundError，PDF 完全无法解析。不只是表格，整个 PDF 解析都卡在这里。
- **RapidOCR 路径问题**：RapidOCR 模型文件实际已随包安装在 site-packages（约 31MB），但 docling 设了 DOCLING_ARTIFACTS_PATH 后期望在 `docling/RapidOcr/` 目录找，路径不匹配导致 OCR 失败。需复制文件到正确位置，不是重新下载。
- 图片信息全丢：picture block text 为空，向量化空字符串，无效 chunk（待 LLM/VLM 方案解决）
- TXT 编码硬伤：硬编码 UTF-8，GBK/GB2312 必乱码，修复成本极低
- 扫描件 PDF：代码已有 OCR 重试逻辑，RapidOCR 路径修好后即通
- Excel 缺失：不支持 xlsx/xls/csv，企业场景刚需
- URL 导入缺失：实现成本低
- 多文档检索无范围筛选
- 元数据未填充：title/author/lang 有字段但解析时没写

**真实但需重新定性：**
- 表格切分：核心问题不是"按行拆"，而是独立成块 embedding 质量差，默认改 markdown 融入正文更实际
- 公式降级：formula→PARAGRAPH 属实，但需 CodeFormula 模型（500MB），理工科公式检索是小众刚需
- 原文定位：page/bbox 数据已有，前端没做高亮跳转，不是从零开始
- 目录导航：切分预览页已有章节树，缺文档阅读态大纲

**真实但低优先级/小众：**
- PPTX 按 slide 分页、EPUB、eml、LaTeX、老版 Office、音视频 ASR、在线文档导入

**被高估的：**
- 多列排版：依赖 docling layout 模型能力，模型补全后基本可用
- 切分策略一刀切：八阶段对通用文档够用，按类型模板是优化项不是阻塞项

### 四阶段规划

**阶段一：解析质量加固（低成本快赢，1-2天）**
- **docling tableformer 模型下载**（约 200MB，PDF 解析硬卡点，最高优先级）
- **RapidOCR 文件复制到 docling artifacts 目录**（修复 OCR 路径问题，非重新下载）
- TXT 编码自动检测（charset-normalizer）
- 表格默认值 preserve→markdown
- 扫描件 OCR 验证（tableformer + RapidOCR 就位后端到端测试）
- 页眉页脚重复行自动清理（quality.py 已检测，加自动剔除）
- 文档元数据提取填充（title/author/lang）
- URL 导入（httpx + trafilature/readability）

**阶段二：格式覆盖扩展 + 解析增强（3-5天）**
- Excel 支持（xlsx/csv）：专用解析器（openpyxl）+ 按 sheet/表头/行的切分逻辑
- 图片文件上传 + OCR（png/jpg）
- 开放 docling 已支持但未开放的格式（doc/ppt/epub/eml 等，加扩展名即可）
- 压缩包批量上传（zip）
- **PyMuPDF 解析预检测**（pdfkit-py 调研结论，08-15）：装 PyMuPDF（~50MB，无系统依赖），在 docling 解析前做三件事：
  1. **CID 乱码检测**：逐页读文本层，检测 `(cid:xxx)` 模式 + 可读字符占比 <10%，乱码页强制走 OCR（当前 docling 不检测 CID 乱码，CCF PDF 文本层仅 787 字符即此类问题）
  2. **逐页类型检测**：5 分类（text/scanned/scanned_with_ocr/mixed/empty），替代当前全局平均阈值（平均<50字符/页），只对扫描页启用 OCR，避免"部分页扫描"误判；参考 pdfkit-py `_detect_page_types`（全页覆盖图片判定 scanned_with_ocr）
  3. **加密/伪加密检测**：PyMuPDF `is_encrypted` + 空密码解锁，加密 PDF 提前报错而非解析出空内容
  - 不引入 camelot/tabula/pytesseract（tableformer/RapidOCR 更优）；不引入 pdfkit-py CLI 包，直接用 PyMuPDF API 写预检测逻辑

**阶段三：检索与消费体验（3-5天）**
- 原文 bbox 高亮定位（前端 PDF 渲染+坐标高亮）
- 多文档范围检索（文档筛选器）
- 图片信息向量化（VLM 生成描述后入库，依赖阶段四 LLM）
- 切分模板（按文档类型预设参数组合）

**阶段四：本地 LLM + RAG 问答（3-5天，依赖本地 LLM 服务）**
- llama-server 部署 Qwen3-VL-4B GGUF（OpenAI 兼容服务）
- LLM 辅助切分对接验证（已有功能，配置地址即可）
- RAG 问答聊天：后端 `/api/chat`（检索→拼 prompt→流式生成），前端第七页聊天界面
- 图片描述生成：解析时对图片块调 VL 模型生成描述，存入 block.text；图片截图用阶段二引入的 PyMuPDF 按 block bbox 渲染区域为 PNG（比 docling 自带图片导出更灵活）
- 引用来源高亮（回答中标注来源 chunk，点击跳转原文）

**阶段五：产品化（持续）**
- 文档自动摘要/标签
- 文档版本管理
- 阅读态目录导航
- 音视频 ASR
- 在线文档导入（飞书/Notion 等）

## 模型状态

| 模型 | 大小 | 状态 |
|------|------|------|
| BAAI/bge-m3 | 2189 MB | 就绪 |
| BAAI/bge-reranker-v2-m3 | 2187 MB | 就绪（transformers 原生） |
| Qwen3-Embedding-0.6B | 761 MB | 就绪 |
| Qwen3-Embedding-4B | 1394 MB | 就绪 |
| docling layout-heron | 164 MB | 就绪 |
| docling tableformer | ~470 MB（accurate 203M + fast 139M） | **就绪（08-14 下载完成）** |
| docling RapidOCR | ~31 MB | 就绪（已复制到 docling/RapidOcr/） |
| docling CodeFormula | ~500 MB | 未下载（公式场景，低优先级） |
| Qwen3-VL-4B-Instruct (FP16) | 8476 MB | 已有（D:\3AIC\models），6GB 显存无法直接加载 |
| Qwen3-VL-4B GGUF Q4_K_M | ~2381 MB | 待下载（ModelScope，阶段四） |
| Qwen3-VL-4B mmproj Q8_0 | ~432 MB | 待下载（视觉编码器，阶段四） |

## 环境信息

- Python venv：`D:\1RAG\Tessera\.venv`（Python 3.13，WorkBuddy）
- GPU：RTX 4050 Laptop 6GB；torch 2.13+cu126
- 后端启动：PowerShell Start-Process，端口 8766，日志 `backend/data/logs/`
- 前端启动：`node node_modules/vite/bin/vite.js`，端口 5173
- 网络：hf-mirror.com 曾不稳定，modelscope.cn 可用但无 docling 镜像

## 注意事项

- 回退代码后必须检查 vite.config.ts 的 target 是否为 8766（曾被回退成 8765 导致代理到 TRAE 生日贺卡项目）
- 杀进程按端口精确杀，不要 `Get-Process python | Stop-Process`
- .gitignore 排除 .venv/、backend/data/、node_modules/、*.db


---


---

## 历史归档

2026-08-12 至 2026-10-08 的详细进展、质检记录与走查报告已移至 [docs/progress-archive.md](docs/progress-archive.md)；bug 归因以 [PAIN_POINTS.md](PAIN_POINTS.md) 为唯一事实源。
