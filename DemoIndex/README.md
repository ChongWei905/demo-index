# DemoIndex

DemoIndex 是一个面向长篇 PDF 研究报告的检索与证据组织系统。

它的目标不是只把 PDF 转成文本，而是把一份报告加工成两层可检索结构：

- 文档树索引：保留报告的章节层级、标题、摘要和父子关系
- 全局切块索引：把叶子章节切成可做向量召回和词法召回的 chunk

在此基础上，DemoIndex 提供一条完整链路：

1. `PDF / Markdown -> PageIndex 风格文档树`
2. `文档树 -> PostgreSQL document_sections`
3. `叶子章节 -> section_chunks 全局索引`
4. `Query -> Stage 1~5 检索 -> answer-ready evidence bundle`

这份 README 既覆盖常规项目说明，也会详细解释 Stage 1~5 的策略，方便给同事做技术讲解。

## 目录

- [项目定位](#项目定位)
- [当前能力](#当前能力)
- [核心架构](#核心架构)
- [数据模型](#数据模型)
- [索引创建过程](#索引创建过程)
- [Stage 1~5 检索策略](#stage-15-检索策略)
- [仓库结构](#仓库结构)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [常用命令](#常用命令)
- [Python API](#python-api)
- [配置项总览](#配置项总览)
- [调试与埋点](#调试与埋点)
- [测试](#测试)
- [局限与设计取舍](#局限与设计取舍)
- [相关文档](#相关文档)
- [License](#license)

## 项目定位

DemoIndex 主要解决这类问题：

- 语料是成百上千篇行业研究报告
- 每篇报告通常有 40~100 页
- 报告内部有明显章节结构
- 不同报告之间会出现重复结论、相近结论、甚至相反结论
- 最终需求不是“找一段相似文本”，而是“找得到、定得准、讲得清”

因此，DemoIndex 采用双层索引：

- 全局层：跨文档召回相关 chunk
- 单文档层：回到文档树里定位、扩展并组织证据

## 当前能力

当前实现已经覆盖两条主链路。

### 1. 构建链路

- PDF 或 Markdown 转成 PageIndex 风格文档树 JSON
- 可选写入 PostgreSQL 的 `document_sections`
- 可选构建全局 `section_chunks` 向量索引与词法索引
- 支持结构化 debug log、阶段耗时、token 消耗埋点

### 2. 检索链路

- Stage 1：查询理解
- Stage 2：全局召回与融合
- Stage 3：树内定位
- Stage 4：上下文扩展
- Stage 5：证据打包

最终可以返回 answer-ready 的 evidence bundle，而不是零散 chunk。

## 核心架构

```mermaid
flowchart TD
    A["PDF"] --> B["Build Tree Pipeline"]
    B --> C["PageIndex-style Tree JSON"]
    C --> D["document_sections"]
    C --> E["Leaf-first Chunking"]
    E --> F["section_chunks"]

    Q["User Query"] --> G["Stage 1 Query Understanding"]
    G --> H["Stage 2 Global Recall"]
    F --> H
    D --> I["Stage 3 Tree Localization"]
    H --> I
    I --> J["Stage 4 Context Expansion"]
    J --> K["Stage 5 Evidence Packaging"]
    K --> L["Evidence Bundle"]
```

高层理解可以记成一句话：

`全局 chunk 负责把文档找出来，文档树负责把答案位置找准确。`

## 数据模型

### 1. `document_sections`

这是单文档树索引表，每一行对应文档树里的一个 section/node。

核心字段：

- `section_id`
- `parent_id`
- `doc_id`
- `node_id`
- `title`
- `depth`
- `summary`

这里的 `section` 可以近似理解为“树节点在数据库里的标准化表示”。

### 2. `section_chunks`

这是全局召回主表，每一行对应一个叶子章节切出来的 chunk。

核心字段：

- `chunk_id`
- `doc_id`
- `section_id`
- `node_id`
- `chunk_index`
- `title`
- `title_path`
- `page_index`
- `chunk_text`
- `search_text`
- `token_count`
- `text_hash`
- `embedding`

当前采用 `leaf-first` 策略，只切叶子章节，避免父子章节内容重复污染全局索引。

## 索引创建过程

这一节只讲构建阶段，也就是 `PDF / Markdown -> 文档树 -> PG 索引` 这条链路。

### 1. 统一入口

无论是 CLI 还是 Python API，最终都走同一个入口：

- `build_pageindex_tree(...)`

它会统一处理：

- 输入类型识别
- PDF / Markdown 建树
- 可选写入 `document_sections`
- 可选构建并写入 `section_chunks`
- 调试日志、耗时和 token 埋点

有一个很重要的行为要注意：

- `--write-global-index` 会隐式启用树写库，也就是会同时写 `document_sections` 和 `section_chunks`
- `--write-postgres` 只写文档树，不构建全局 chunk 索引

### 2. PDF 建树流程

PDF 的默认策略不是单一路径，而是“候选树编排 + 评分选优”。

构建顺序可以概括成：

1. 先抽每页的文本、版式块、标题候选和页码信息
2. 再尝试检测 TOC 候选页并抽取 `outline_entries`
3. 对 TOC 做一轮置信度评估，生成 `toc_assessment`
4. 根据 `DEMOINDEX_BUILD_PDF_STRATEGY` 组装候选策略
5. 对候选树做统一归一化与打分，选出最终树

当前支持的 PDF 策略有四种：

- `auto`
  - 默认值
  - 高置信 TOC 优先走 `toc_seeded`
  - 无 TOC 或弱 TOC 时优先走 `pageindex_native`
  - 前两条路径失败或质量太差时退回 `layout_fallback`
- `toc_seeded`
  - 用 TOC 条目生成 `seeded_outline`，再继续往下构树
- `pageindex_native`
  - 直接复用 PageIndex 原生的无 TOC / 弱 TOC 构树链路
  - 对“文本能抽出来，但目录不稳定”的 PDF 通常比简单 heading fallback 更接近官方 API
- `layout_fallback`
  - 用页面 heading candidates 直接生成兜底树
  - 目标是避免空树，不保证层级最优

候选树的质量判断主要看这些通用信号：

- 是否非空
- 页码覆盖是否合理
- 根节点数是否异常膨胀
- 最大深度和深度分布是否像“章节树”
- 标题编号是否自洽，例如 `01` / `1.1` / `（一）`
- 标题是否真的出现在对应页的 heading candidates 中
- 是否出现大量句子型 root 或图表说明型 root

这一步的目标不是“先把树建出来就算完”，而是尽量避免把一份报告退化成几十个平铺 root。

### 3. Markdown 建树流程

Markdown 走同一个统一入口，但不需要 PDF 的 TOC 检测和候选策略选择。

当前支持三种布局模式：

- `auto`
- `h1_forest`
- `page_per_page`

当没有显式传 `--markdown-layout` 时，会先读取 `DEMOINDEX_BUILD_MARKDOWN_LAYOUT`，再回落到代码默认值 `auto`。

### 4. 从文档树到两层索引

最终选中的文档树会继续走两步标准化写入：

1. 文档树节点标准化后写入 `document_sections`
2. 只对叶子章节做切块、embedding 和写库，落到 `section_chunks`

这里为什么坚持 `leaf-first`：

- 避免父节点和子节点同时进全局索引，造成重复召回
- 减少向量和词法索引里的冗余文本
- 保持 Stage 2 的候选更像“进入文档树的 anchor”，而不是一堆层级重叠的 chunk

### 5. 构建阶段最常看的 artifacts

开启 `--debug-log` 后，排查建树问题时最常用的是这些文件：

- `outline_entries.json`
  - 从 TOC 页抽出的目录条目
- `toc_assessment.json`
  - TOC 置信度评估结果，以及候选页、映射率、层级信号等统计
- `seeded_outline.json`
  - 进入正式构树前的种子大纲
- `candidate_selection.json`
  - 每条候选策略的状态、耗时、树统计和最终选中结果
- `pageindex_raw.json`
  - 最终选中候选树对应的原始树结构
- `postgres_write.json`
  - `document_sections` 写库摘要
- `global_index_write.json`
  - `section_chunks` 切块与写库摘要
- `run_summary.json`
  - 本轮构建的阶段耗时、token 和调用统计

如果你是在对比“为什么官方 API 的树更深，而本地树更平”，优先看 `toc_assessment.json` 和 `candidate_selection.json`。

## Stage 1~5 检索策略

这一部分是整个系统最重要的设计说明。

### 总览

#### Stage 1：Query Understanding

目标：

- 理解 query 的语言、时间范围、意图和关键词
- 给后续召回提供结构化输入

输入：

- 用户 query

输出：

- `QueryUnderstanding`

核心字段：

- `raw_query`
- `normalized_query`
- `language`
- `intent`
- `terms`
- `time_scope`
- `llm_enriched`

设计要点：

- 核心代码保持通用，不把行业词典硬编码进主逻辑
- 默认先走规则解析
- 只有 query 明显稀疏或模糊时，才触发一次 LLM enrichment
- 领域知识如果需要注入，应通过外置 retrieval profile，而不是写死在项目代码里

一句话理解：

`Stage 1 的目标不是回答问题，而是把 query 变成更适合检索的中间表示。`

#### Stage 2：Global Candidate Recall

目标：

- 在全库 chunk 中做跨文档召回
- 先找到“哪些文档、哪些 section 可能相关”

当前实现同时跑两条分支：

- dense recall
- lexical recall

##### dense recall

- 用 `text-embedding-v4` 对 query 做 embedding
- 在 `section_chunks.embedding` 上做向量召回

##### lexical recall

- 基于 `title`、`title_path`、`search_text`
- 使用 PostgreSQL `pg_trgm`
- 对中文 query 做更友好的 term 提取和加权匹配
- 不再依赖整句严格 `%` 过滤

##### 融合方式

- dense 与 lexical 结果按 `chunk_id` 做 RRF 融合
- 输出 fused chunk hits

##### 聚合方式

- 按 `doc_id` 聚合得到 `doc_candidates`
- 按 `doc_id + section_id` 聚合得到 `section_candidates`

这里借鉴了 PageIndex 的核心思想：

- chunk 不是最终对象
- chunk 是给 node/section 打分的证据

一句话理解：

`Stage 2 的任务是先把相关文档和潜在入口 section 找出来。`

#### Stage 3：Tree Localization

目标：

- 进入单文档以后，不直接停在 chunk
- 回到文档树，把最相关的 section/node 定位出来

当前支持两种模式：

- `heuristic`
- `hybrid`

##### heuristic

- 根据 Stage 2 的 section anchors 建一个局部候选池
- 候选池包含：
  - anchor section
  - anchor descendants
  - anchor ancestors
  - 一跳 siblings
  - 必要时 whole-doc fallback
- 用确定性规则打分

##### hybrid

- 先跑完整 heuristic
- 再对每个文档的一小份 shortlist 做一次 LLM rerank
- 单文档 rerank 失败时，只对该文档回退到 heuristic

这是 DemoIndex 和公开 PageIndex 方案最重要的区别之一：

- PageIndex 公开方案已经有 `chunk -> doc`、`chunk -> node`
- DemoIndex 在此基础上显式加入了 `section anchor` 作为单文档入口先验

一句话理解：

`Stage 3 解决的是“进了文档以后，应该先看树的哪一支”。`

#### Stage 4：Context Expansion

目标：

- 把 Stage 3 定位到的 focus section 扩成一个 bounded、可阅读、可回答问题的局部上下文

当前实现会围绕 focus section 扩展：

- ancestors
- descendants
- siblings
- supporting chunks
- chunk neighbors

并拼成一个 `answer_context_text`。

扩展顺序与裁剪策略都做了明确约束：

- 先保留 focus summary
- 再视预算加入 ancestor/descendant/sibling summaries
- 最后加入 supporting chunk 和 neighbor chunk
- 截断时优先裁掉 neighbor，再裁 sibling，再裁 descendant，再裁 ancestor
- focus summary 不会被删掉

一句话理解：

`Stage 4 把“命中点”扩成“一个能拿去回答的局部上下文”。`

#### Stage 5：Evidence Packaging

目标：

- 把多个 expanded contexts 打包成最终 evidence bundle
- 给下游回答模块一个稳定、可追溯、可引用的输入

当前支持两种模式：

- `heuristic`
- `hybrid`

##### heuristic

- 去重
- 计算 evidence score
- 按 doc 和全局总量裁剪
- 输出 evidence items
- `relationship_label` 默认为 `unlabeled`

##### hybrid

- 先生成完全相同的 heuristic package
- 再对 shortlist evidence items 做一次全局关系标注
- 允许的关系标签：
  - `supports`
  - `conflicts`
  - `related`
- LLM 失败时整体退回 heuristic package，不破坏输出结构

一句话理解：

`Stage 5 把局部上下文整理成最终可交给回答系统的证据包。`

### 一张表看完 Stage 1~5

| Stage | 目标 | 主要输入 | 主要输出 | 是否调用 LLM |
| --- | --- | --- | --- | --- |
| Stage 1 | 理解 query | query | `QueryUnderstanding` | 可选 |
| Stage 2 | 跨文档召回 | query + `section_chunks` | `chunk_hits` / `doc_candidates` / `section_candidates` | embedding 必需 |
| Stage 3 | 树内定位 | Stage 2 结果 + `document_sections` | `localized_sections` | `hybrid` 模式可选 |
| Stage 4 | 上下文扩展 | Stage 3 结果 + `document_sections` + `section_chunks` | `expanded_contexts` | 否 |
| Stage 5 | 证据打包 | Stage 4 结果 | `evidence_items` / `evidence_docs` | `hybrid` 模式可选 |

## 仓库结构

```text
DemoIndex/
├── __init__.py
├── __main__.py
├── debug.py
├── env.py
├── global_index.py
├── llm.py
├── models.py
├── pdf.py
├── pipeline.py
├── postgres_store.py
├── retrieval.py
├── run.py
├── artifacts/
├── docs/
└── tests/
```

重点文件说明：

- `pipeline.py`
  - 统一构建入口，负责 PDF / Markdown 分流、文档树生成、PostgreSQL 写入与全局索引构建
- `build_md_pageindex.py`
  - Markdown PageIndex builder，支持 `h1_forest` 与 `page_per_page` 两种布局
- `retrieval.py`
  - Stage 1~5 检索主逻辑
- `postgres_store.py`
  - `document_sections` / `section_chunks` 的持久化
- `global_index.py`
  - 叶子章节切块与索引对象生成
- `llm.py`
  - chat / embedding 客户端封装
- `debug.py`
  - debug log、耗时、token 埋点
- `docs/retrieval_config.md`
  - Stage 1~5 配置总表

## 环境要求

建议环境：

- Python 3.11+
- PostgreSQL 16+
- `pgvector`
- `pg_trgm`
- 至少一套 chat API key
- 至少一套 embedding API key

当前 CLI 会在缺少依赖时自动尝试重进 `PageIndex/.venv`，所以在这个仓库里最省事的做法通常是直接复用：

- `PageIndex/.venv`

## 配置入口

DemoIndex 现在统一从下面两个文件理解运行配置：

- 主配置文件：`DemoIndex/.env`
- 唯一模板来源：`DemoIndex/.env.example`

推荐初始化方式：

```bash
cp /Users/weichong/Documents/new_working_area/file_tree/DemoIndex/.env.example \
  /Users/weichong/Documents/new_working_area/file_tree/DemoIndex/.env
```

优先级固定为：

- 显式 Python 参数 / CLI 参数
- `DemoIndex/.env`
- 代码默认值

说明：

- `input_path`、`query`、`output_json` 这类每次调用都变化的输入参数，不放进 env
- DemoIndex 不再把 `PageIndex/.env` 作为主配置入口
- 旧变量名如 `DASHSCOPE_API_KEY`、`OPENAI_API_KEY`、`PAGEINDEX_*`、`DATABASE_URL` 不再是 DemoIndex 的推荐配置方式，需要手动迁移到新的 `DEMOINDEX_*` 命名

## Chat / Embedding API

DemoIndex 现在把 chat 和 embedding API 完全拆开配置：

- chat 读取 `DEMOINDEX_LLM_*`
- embedding 读取 `DEMOINDEX_EMBEDDING_*`

支持的 provider 固定为：

- `dashscope`
- `openai`

常见组合：

- chat 走 DashScope，embedding 走 OpenAI
- chat 走 OpenAI，embedding 走 DashScope
- 两边都走同一个 provider，但使用不同 key / base URL / model

完整变量清单见：

- `DemoIndex/.env.example`
- `docs/retrieval_config.md`

## 快速开始

### 1. 准备 `DemoIndex/.env`

```bash
cp /Users/weichong/Documents/new_working_area/file_tree/DemoIndex/.env.example \
  /Users/weichong/Documents/new_working_area/file_tree/DemoIndex/.env
```

最小可用示例：

```dotenv
DEMOINDEX_DATABASE_URL=postgresql://demoindex:demoindex@127.0.0.1:5432/demoindex

DEMOINDEX_LLM_API_PROVIDER=dashscope
DEMOINDEX_LLM_API_KEY=your-chat-key

DEMOINDEX_EMBEDDING_API_PROVIDER=openai
DEMOINDEX_EMBEDDING_API_KEY=your-embedding-key
DEMOINDEX_RETRIEVAL_EMBEDDING_MODEL=text-embedding-3-large
DEMOINDEX_BUILD_GLOBAL_INDEX_MODEL=text-embedding-3-large
DEMOINDEX_EMBEDDING_DIMENSIONS=1024
```

### 2. 构建文档树与全局索引

```bash
/Users/weichong/Documents/new_working_area/file_tree/PageIndex/.venv/bin/python -m DemoIndex.run run \
  --input-path /Users/weichong/Documents/new_working_area/file_tree/DemoIndex/docs/game2025report.pdf \
  --artifacts-dir /Users/weichong/Documents/new_working_area/file_tree/DemoIndex/artifacts/demo_build \
  --write-global-index \
  --debug-log
```

说明：

- `--write-global-index` 会自动补写 `document_sections`
- PDF 建树策略通过 `DemoIndex/.env` 里的 `DEMOINDEX_BUILD_PDF_STRATEGY` 控制，默认是 `auto`
- 如果要专门复现实验，可以把它改成 `toc_seeded`、`pageindex_native` 或 `layout_fallback`

Markdown 也走同一个入口：

```bash
/Users/weichong/Documents/new_working_area/file_tree/PageIndex/.venv/bin/python -m DemoIndex.run run \
  --input-path /absolute/path/to/combined_document.md \
  --markdown-layout auto \
  --write-global-index \
  --debug-log
```

`markdown_layout` 支持：

- `auto`
- `h1_forest`
- `page_per_page`

当 CLI 没显式传 `--markdown-layout` 时，会先读 `DEMOINDEX_BUILD_MARKDOWN_LAYOUT`，再回落代码默认值 `auto`。`auto` 的规则是：

- 文件内存在 `<!-- page:N -->` 注释时，自动用 `page_per_page`
- 否则使用 `h1_forest`

### 3. 仅跑 Stage 1 + 2

```bash
/Users/weichong/Documents/new_working_area/file_tree/PageIndex/.venv/bin/python -m DemoIndex.run retrieve \
  --query "2024 全球手游 CPI 和留存趋势" \
  --debug-log
```

### 4. 跑到 Stage 3

```bash
/Users/weichong/Documents/new_working_area/file_tree/PageIndex/.venv/bin/python -m DemoIndex.run retrieve-tree \
  --query "2024 全球手游 CPI 和留存趋势" \
  --stage3-mode hybrid \
  --debug-log
```

### 5. 跑完整 Stage 1~5

```bash
/Users/weichong/Documents/new_working_area/file_tree/PageIndex/.venv/bin/python -m DemoIndex.run retrieve-evidence \
  --query "2024 全球手游 CPI 和留存趋势" \
  --stage3-mode hybrid \
  --stage5-relation-mode heuristic \
  --debug-log
```

## 常用命令

### 构建与比较

```bash
python -m DemoIndex.run run --input-path <pdf-or-md> [--write-postgres] [--write-global-index]
python -m DemoIndex.run compare --actual-json <actual> --expected-json <expected>
python -m DemoIndex.run benchmark-build --output-dir <benchmark-dir> [--pdf-path <pdf>] [--strategy auto]
```

### 检索

```bash
python -m DemoIndex.run retrieve --query "<query>"
python -m DemoIndex.run retrieve-tree --query "<query>"
python -m DemoIndex.run retrieve-evidence --query "<query>"
```

### 常见输出

- `run`: PageIndex 风格树 JSON、调试日志、全局索引写入报告
- `benchmark-build`: 多策略建树结果、官方树对比报告、候选树 artifacts
- `retrieve`: Stage 1 + 2 的 rich result
- `retrieve-tree`: Stage 1 + 2 + 3 的 tree-localized result
- `retrieve-evidence`: Stage 1~5 的最终 evidence bundle

## Python API

当前对外导出的主要 API：

```python
from DemoIndex import (
    build_pageindex_tree,
    compare_tree,
    parse_query,
    retrieve_candidates,
    localize_sections,
    retrieve_tree_candidates,
    expand_localized_sections,
    package_evidence,
    retrieve_evidence,
)
```

推荐调用层级：

- 只做构建：
  - `build_pageindex_tree(...)`
- 只做 Stage 1 + 2：
  - `retrieve_candidates(...)`
- 跑到 Stage 3：
  - `retrieve_tree_candidates(...)`
- 跑完整 Stage 1~5：
  - `retrieve_evidence(...)`

这些 Python API 现在和 CLI 一样遵守同一套优先级：

- 显式函数参数
- `DemoIndex/.env`
- 代码默认值

## 配置项总览

Stage 1~5 的全部检索配置和 env 对照表已经统一整理到：

- `docs/retrieval_config.md`

这里不重复列全表，只给一个阅读建议：

- 想调 query parsing：看 Stage 1
- 想调召回范围和融合：看 Stage 2
- 想调树定位策略：看 Stage 3
- 想调上下文范围：看 Stage 4
- 想调证据输出规模与关系标注：看 Stage 5

如果只想先看主模板，直接打开：

- `DemoIndex/.env.example`

## 调试与埋点

DemoIndex 支持比较完整的调试信息输出。

开启方式：

- CLI:
  - `--debug-log`
  - `--debug-log-dir`

常见调试产物包括：

- `debug.log.jsonl`
- `run_summary.json`
- `toc_assessment.json`
- `candidate_selection.json`
- `postgres_write.json`
- `global_index_write.json`
- 最终检索 JSON

当前埋点覆盖：

- 阶段耗时
- chat / embedding 调用次数
- prompt/completion/total tokens
- Stage 2 召回统计
- Stage 3 anchor、candidate pool、rerank
- Stage 4 扩展结果
- Stage 5 evidence packaging 与关系标注

## 测试

运行全部测试：

```bash
/Users/weichong/Documents/new_working_area/file_tree/PageIndex/.venv/bin/python -m pytest DemoIndex/tests -q
```

当前测试覆盖主要包括：

- 构建链路关键逻辑
- Stage 1~5 检索主流程
- PostgreSQL 集成
- hybrid fallback 行为

## 局限与设计取舍

### 1. 目前仍偏向研究报告场景

虽然 Stage 1 核心逻辑已经做成通用模式，但整个系统从索引策略到上下文扩展方式，仍然更适合：

- 章节明确
- 文档较长
- 结构化总结较重要

的长文档场景。

### 2. Stage 3/Stage 5 的 hybrid 会引入额外成本

- Stage 3 `hybrid` 会按文档做 rerank
- Stage 5 `hybrid` 会做全局 evidence relation labeling

如果更关注成本和延迟，可以优先使用：

- Stage 3 `heuristic`
- Stage 5 `heuristic`

### 3. 当前默认主方案是 Anchor-First

当前实现主路径是：

`chunk -> doc + section anchor -> subtree-first localization`

也保留了 doc-entry-only 的设计文档，但还不是当前主实现模式。

## 相关文档

- `docs/retrieval_config.md`
  - Stage 1~5 配置总表
- `docs/search_plan_anchor_subtree.md`
  - 当前主方案设计文档
- `docs/search_plan_doc_entry_only.md`
  - fallback 方案设计文档
- `docs/retrieval_profile.game_example.json`
  - 外置 retrieval profile 示例

## License

这个子目录当前没有单独声明 License。

如果准备对外发布，建议在仓库根目录或 `DemoIndex/` 目录补充明确的 License 与发布说明。
