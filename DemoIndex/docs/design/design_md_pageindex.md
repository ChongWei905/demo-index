# DemoIndex：从 `combined_document.md` 生成 PageIndex 风格 JSON

本文档描述在 `DemoIndex` 工程中，如何由 `docs/results_with_vlm/game2025report_v7/combined_document.md` 生成与现有 `out.json` **兼容并扩展** 的结构化索引。实现思路**参考** PageIndex-main 的 `page_index_md.py`（标题解析、按层级建树、可选摘要），但**不照搬**其仅适用于纯 MD 目录树的输出形状；需同时支持 VLM 合并稿中的 `<!-- page:N -->` 分页注释与「按一级标题分段的森林」布局。

---

## 1. 目标输出：顶层外壳（envelope）

生成一个 JSON 对象，字段如下。

| 字段 | 类型 | 说明 |
|------|------|------|
| `doc_id` | string (UUID) | 文档标识；可配置为固定值或与路径/内容绑定的确定性 UUID，便于重复生成一致。 |
| `status` | string | 处理状态，例如 `"completed"`。 |
| `retrieval_ready` | boolean | 是否已完成可供下游检索链路消费的校验/后处理。 |
| **`line_count`** | **integer** | **整份 Markdown 的行数**（与 `page_index_md.py` 中 `line_count = markdown_content.count('\n') + 1` 语义一致），表示源 `combined_document.md` 的规模。 |
| **`summary`** | **string** | **整份文档的摘要**（见第 4 节「文档级 summary」），与 PageIndex-main 中可选的 `doc_description` 角色类似，但字段名统一为 `summary`。 |
| `result` | array | 森林：每个元素是一棵树的根节点（见第 2 节）。 |

说明：

- 相比最初只含 `doc_id` / `status` / `retrieval_ready` / `result` 的样例，本设计**显式增加**顶层 **`line_count`** 与 **`summary`**，以满足与 PageIndex-main 对齐的元信息需求。
- 顶层 **`summary`** 表示**全文**；节点上的 **`summary`** 表示**该节**（见第 2 节与第 4.3 节），二者层级不同，互不替代。

---

## 2. `result` 中每个节点的字段

每个节点为对象，字段如下。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string | 是 | 展示用标题（可对原始 ATX 标题做规范化，如去掉「前言：」等前缀）。 |
| `node_id` | string | 是 | 如 `0000`、`0001`，按约定遍历顺序分配。 |
| `page_index` | integer | 是 | 该节起始位置对应的页码，由正文中最近一次出现的 `<!-- page:N -->` 推导。 |
| `text` | string | 是 | 该节正文片段（从对应标题行起至下一同级/更高级标题前，含中间的 page 注释等）。 |
| **`summary`** | **string** | **条件** | **该节内容的摘要**（见第 4 节「节点级 summary」）；若关闭摘要生成则可设为空字符串或省略（实现阶段二选一并在实现中固定）。 |
| `nodes` | array | 否 | 子节点列表；叶子节点可无此字段或为空数组。 |

与 PageIndex-main 的对应关系：

- `line_num`：本设计**不输出**到 JSON（实现内部仍可用行号）；对外用 `page_index` +结构表达位置。
- **`summary`**：与 `page_index_md.py` 中叶子 `summary`、内部节点 `prefix_summary` 的意图一致；为简化对外 JSON，**统一使用字段名 `summary`**：内部节点可为「本节引言/压缩概括」，叶子为「整段压缩」；若需与原版完全一致的可区分语义，可在后续版本增加 `prefix_summary`，当前设计以单一 `summary` 为主。

---

## 3. 文档结构算法（与实现对齐的约束）

1. **分页映射**  
   扫描 `<!-- page:N -->`，为每一行绑定「当前页码」，供填写 `page_index`。

2. **按一级标题分段（森林）**  
   每个单独成段的 `# ...`（ATX 一级标题，非 `##`）对应 `result` 数组中的**一个根节点**。第一段通常对应主报告；后续 `# 研究方法`、`# 关键看点` 等为并列顶层项。

3. **段内层级树**  
   在每段内，用 `##` / `###` / `####` 等构建父子关系；算法与 `page_index_md.py` 的栈式建树同类：每个标题覆盖从其标题行到「下一个同级或更高级标题」之前的行范围。

4. **标题规范化**  
   `normalize_display_title`：例如 `## 前言：移动游戏勇攀新巅峰` → `title` 为 `移动游戏勇攀新巅峰`；根标题可与正文空格规则对齐（如 `2025年游戏应用洞察报告`）。

5. **`node_id` 分配**  
   对整片森林按约定顺序（深度优先先序、子节点按文档出现顺序）分配 `0000`、`0001`、…，与现有 `out.json` 可对比回归。

6. **代码块**  
   若未来 MD 含 fenced code，标题识别需跳过代码块（与 `page_index_md.py` 一致）。

---

## 3.1 页驱动 PageIndex（通用 Markdown，无 combined 金标准）

本节描述**第二种布局**，用于任意带 `<!-- page:N -->` 的 Markdown（如 MinerU 导出稿）：**不依赖** `combined_document.md` 做 gold 对齐；**不要求**与 H1 森林模式或 `_ref_combined.json` 结构一致。

**实现状态（已与文档对齐）**

- 已在 [`build_md_pageindex.py`](../../build_md_pageindex.py) 落地：通过参数 **`layout`** 选择布局——`h1_forest`（默认，与第 3 节一致）或 **`page_per_page`**（本节页驱动）。命令行支持 **`--layout h1-forest`**（默认）与 **`--layout page-per-page`**（与 **`--input-md`** 联用）。
- 页驱动相关入口函数包括：`group_line_ranges_by_page`、`build_forest_page_per_page_with_doc_root`；`build_pageindex_payload` / `build_pageindex_payload_from_lines` 及其同步包装均接受 **`layout`**。
- 原用于 MinerU与 combined 对齐的 **`mineru_canonicalize.py` 模块已删除**。MinerU 类分页稿使用**同一 CLI**：`python build_md_pageindex.py --input-md <path.md> --layout page-per-page`（`layout`即 **`page_per_page`**），**无需**单独的 `build_mineru_pageindex.py`。

### A. 核心算法（页驱动）

#### 按页切段

- 使用现有 [`parse_page_comments(lines)`](../../build_md_pageindex.py) 得到每行起始处的逻辑页码 `page_by_line`。
- 将**连续相同页码**的行合并为闭区间 `[start, end]`，得到若干页块；第 `k` 页对应一个行区间（页码可能非连续递增，以注释为准）。
- **分页注释行本身**仍归属「**切换前**」的页码（与当前 `parse_page_comments` 一致：`out.append(current)` 后再 `current = int(...)`），避免与现有工具行为不一致。

#### 每页节点的 `title`

- 在该页行区间内、**跳过 fenced code**（与 `iter_atx_headers` 一致），自上而下找**第一个 `level == 1`** 的 ATX 标题，取 `raw_title`，经 **`normalize_display_title(raw_title, 1)`** 作为该页节点的 `title`。
- **回退 1**：该页无 `#` 时，尝试第一个 `##`，用 **`normalize_display_title(raw_title, 2)`**。
- **回退 2**：该页既无 `#` 也无 `##` 时，在构建流程中于写 JSON 之前调用 **`generate_llm_page_titles_when_no_heading`**：若 `llm_factory` 可用且客户端创建成功，则对该页 **`text`** 调用大模型生成**不超过** `PageIndexOptions.page_title_llm_max_chars`（默认 **50**）字的**一句中文标题**；否则对该页正文做**启发式截断**（单行空格化后取前 50 字，过长加省略号；正文为空时仍为 **`第 N 页`**）。CLI 对应 **`--page-title-max-chars`**。

#### 每页节点的 `text`

- 将该页区间内**全部行**拼成该页节点的 `text`（行之间 `\n` 连接，与现有 `_join_lines` 类语义一致）。
- **同一页多个 `#`**：仅**第一个** `#` 参与上述 `title`；其余 `#` 保留在 `text` 中，**不**拆成多个顶层页节点（除非未来增加「一页多节点」模式）。
- **分页注释是否保留在 `text` 中**：默认**保留**（便于对照 PDF/调试）；若需更干净的 JSON，可通过选项**去掉页内** `<!-- page:N -->` 行（实现期约定）。

#### `nodes`（子树）

- **默认（推荐）**：每页只生成**一个**节点，**`nodes`: `[]`**；该页内所有 `##`～`######` 仅出现在 `text` 中，行为简单、可预测。
- **可选**：在该页区间内，对**第一个 H1 之后**的 `##`～`######` 用与现网相同的**栈式建树**（`build_tree_from_flat_nodes` 同类逻辑）挂到该页节点下；**仍不要求**与 combined 的段划分一致。建议由参数控制（例如 `--page-subtrees`），默认关闭。

#### `page_index`

- 每页节点：`page_index` 取该页逻辑页码（与 `page_by_line[start]` 一致）。

### B. 顶层结构：文档根 + 按页子节点（与 combined 第一棵树根对齐）

为满足与 **现行 `combined_document.md` 处理行为一致**的约定（见下），**`result` 建议为单根树**：长度为 1 的数组，唯一元素为**文档根**。

1. **文档根的 `title` / `text`（与 combined 行为一致）**  
   - 与第 3 节 **H1 森林**模式下、**全篇第一个 H1 段**的根节点**同一套规则**：复用 [`build_section_root_and_flat_nodes`](../../build_md_pageindex.py) 中对「第一个 `#` 所在段（至下一个 `#` 之前）」的截断逻辑。  
   - 即：**`title` = `normalize_display_title(全篇首个 H1 的 raw_title, 1)`**；**`text` = 从该 H1 行起，至该段内第一个 `##` 之前**（**不是**整篇、**不是**「文件首段」、**不是**固定报告名）。  
   - 该规则与当前 `combined_document.md` 生成的 **`result[0]` 根节点**在语义上一致（引言可跨多页，直至如 `## 目录` 等第一个段内二级标题）。  
   - **`page_index`**：取该 H1 所在行的 `page_by_line[h1_line]`。

2. **文档根的 `nodes`**  
   - 按**页码顺序**（或文档行顺序对应的页块顺序）挂载**每页一个子节点**；子节点的 `title` / `text` / `page_index` / `nodes` 按 **§3.1.A** 生成。

3. **与纯页列表的差异说明**  
   - 文档根 `text` 为 **H1 段内、首 `##` 前引言**；某页的 `text` 为 **整页全文**。二者在重叠区间可能内容重复，属**有意与 combined 根语义对齐**的代价；若后续需去重，可在实现外再议。

### C. 与旧链路的关系

- **`mineru_canonicalize.py` 已移除**：不再存在 gold 对齐规范化层。  
- **分页稿（含 MinerU 导出）**：`python build_md_pageindex.py --input-md <path> --layout page-per-page`，等价于 `layout="page_per_page"`；**不需要** `combined_document.md` 作为输入。  
- 仍复用：`PageIndexOptions`、`compute_line_count`、摘要生成、`assign_node_ids_preorder`、`strip_forest`、JSON 写出等。

### D. 测试约定

- 使用 `MinerU_markdown_game2025report.md` 等作 **fixture**：断言**页块数量**、每页 **`page_index`**、每页 **`title`** 与「页内首 H1 + 回退」一致等。
- **不断言**与 `_ref_combined.json` 或 H1 森林产物完全一致。

### E. 无 `#` / `##` 时的页节点 `title`（已实现）

[`build_md_pageindex.py`](../../build_md_pageindex.py) 内部 **`_page_node_title`**：页内优先第一个 **`#`**，否则第一个 **`##`**。若二者皆无，节点暂标 **`_needs_llm_page_title`**（内部字段，不出现在最终 JSON），占位 `title` 为 **`第 {page_num} 页`**，随后在 **`build_pageindex_payload` / `build_pageindex_payload_from_lines`** 中先于节点摘要调用 **`generate_llm_page_titles_when_no_heading`** 写入最终标题。

- **LLM**：提示模型仅输出短标题，结果经 **`_sanitize_llm_page_title_line`** 截断至 `page_title_llm_max_chars`（默认 50）。
- **无模型或调用失败**：**`_heuristic_short_page_title`**（正文截断；空正文仍为 **`第 N 页`**）。
- **客户端创建**：当 **`if_add_summary` 为 False** 但存在需补标题的页节点时，仍会尝试 **`llm_factory()`** 以生成页标题（仅摘要关闭时仍可能产生模型调用）。

---

## 4. `summary` 与 `line_count` 的生成约定

### 4.1 `line_count`（顶层）

- 在读取 `combined_document.md` 后计算：  
  `line_count = content.count('\n') + 1`（若文件不以换行结尾，仍与 Python 常见行计数一致）。
- 写入 JSON **根对象**，与 `doc_id`、`status` 等并列。

### 4.2 文档级 `summary`（顶层）

- **输入**：整份 MD 全文或「仅标题树 + 各节短摘录」以控制 token（实现可选）。
- **输出**：一段中文（或与文档语言一致的）短文，概括报告目的、主要章节与结论导向。
- **生成方式**（实现可选其一或组合）：
  - 调用 DemoIndex 已有 LLM 封装（如 `llm.py`）；
  - 或占位策略：首段 H1 下前 N 字 + 各 `result` 根节点 `title` 拼接的启发式摘要（无 API 时降级）。
- 字段名固定为根上的 **`summary`**。

### 4.3 节点级 `summary`（`result` 树中每个节点）

- **叶子节点**：对 `text` 做压缩摘要；若 `text` 短于某 token 阈值，可直接使用 `text` 或略去调用模型（与 `get_node_summary` 思路一致）。
- **内部节点**：可对「标题 + 子节点标题列表 + 首节若干字」生成概括，或对合并后的子摘要再摘要；实现可与 `generate_node_summary` / `prefix_summary` 行为对齐，但 **对外只写 `summary` 一个键**。
- **异步**：若走 LLM，实现可采用 `asyncio.gather` 批量生成（参考 `generate_summaries_for_structure_md`），在 CLI 入口用 `asyncio.run` 统一调度。

### 4.4 与「无 summary」模式的兼容

- 配置项建议：`--if-add-summary`（`yes`/`no`）、`--if-add-doc-summary`（或与顶层 summary 绑定同一开关）。
- 当关闭时：顶层 `summary` 可为 `""`，节点 `summary` 为 `""` 或省略；**`line_count` 仍始终写入**。

---

## 5. 建议文件与函数（更新版）

以下与实现代码 [`build_md_pageindex.py`](../../build_md_pageindex.py) 对齐；含 **`line_count` / `summary`**、**`layout`（§3.1）**。

### 5.1 `build_md_pageindex.py`（核心库：分页、标题、H1 森林）

| 函数 / 类型 | 作用 |
|-------------|------|
| `parse_page_comments(lines)` | 建立每行「行首」所处逻辑页码（含 `<!-- page:N -->` 语义）。 |
| `iter_atx_headers(lines)` | 扫描 `#`～`######`，跳过 fenced 代码块。 |
| `find_h1_line_indices(headers)` | 取出全部一级标题行号。 |
| `normalize_display_title(raw_title, level)` | 生成对外 `title`（如前言前缀、`2025 年` 空格规则）。 |
| `build_section_root_and_flat_nodes(...)` | 单个 H1 段：根节点 `text`（至段内首 `##` 前）+ 段内 `##+` 扁平列表。 |
| `build_tree_from_flat_nodes(flat)` | 将扁平 `##+` 节点栈式挂成子树。 |
| `build_forest_from_markdown(lines, page_by_line)` | **`layout=h1_forest`**：按每个 H1 切段建树，返回森林。 |
| `assign_node_ids_preorder(forest)` | 深度优先先序分配 `node_id`（`0000` 起）。 |
| `strip_internal_fields(tree)` | 去掉 `_line_idx`，保留对外 JSON 字段（含 `summary`）。 |
| `strip_forest(forest)` | 对森林逐棵 `strip_internal_fields`。 |
| `compute_line_count(content)` | 计算顶层 `line_count`。 |
| `async generate_doc_summary(...)` | 生成顶层文档 `summary`（LLM 或启发式）。 |
| `async generate_node_summaries(...)` | 为每个节点填 `summary`（可批量异步）。 |
| `async build_pageindex_payload(md_path, opt, llm_factory, *, layout=...)` | 从文件路径编排全流程；**`layout`**：`h1_forest`（默认）或 `page_per_page`。 |
| `sync_build_pageindex_payload(..., *, layout=...)` | 上式的 `asyncio.run` 包装。 |
| `async build_pageindex_payload_from_lines(lines, opt, llm_factory, *, doc_id_seed=..., layout=...)` | 与上等价，输入为行列表（无文件路径）。 |
| `sync_build_pageindex_payload_from_lines(..., *, layout=...)` | 上式的同步包装。 |

### 5.2 `build_md_pageindex.py`（页驱动与 `layout`，§3.1）

| 函数 / 类型 | 作用 |
|-------------|------|
| **`PageIndexLayout`** | 类型别名：`Literal["h1_forest", "page_per_page"]`，用于 `layout` 参数。 |
| **`group_line_ranges_by_page(page_by_line)`** | 将逐行页码合并为连续区间列表 **`(page_num, start, end)`**（闭区间行下标）。 |
| **`_page_node_title(...)`**（内部） | 返回规范化标题与是否需 LLM/启发式补全；先页内首 `#`，再首 `##`；否则占位 **`第 N 页`** 并标 **`_needs_llm_page_title`**（见 §3.1.E）。 |
| **`_collect_nodes_needing_llm_page_title` / `_heuristic_short_page_title` / `_sanitize_llm_page_title_line`**（内部） | 收集待补标题节点、启发式短标题、清洗模型输出。 |
| **`generate_llm_page_titles_when_no_heading`** | 异步：对无 `#`/`##` 的页节点写入最终 `title`，并去掉内部标记。 |
| **`build_forest_page_per_page_with_doc_root(lines, page_by_line)`** | **`layout=page_per_page`**：单根文档树——根与「全篇首 H1 段」根规则一致（`build_section_root_and_flat_nodes`），**`nodes`** 为按页顺序的「每页一节点」（整页 `text`，默认 **`nodes: []`**）。 |
| **`_build_forest_for_layout(lines, page_by_line, layout)`**（内部） | 按 **`layout`** 分派到 `build_forest_from_markdown` 或 `build_forest_page_per_page_with_doc_root`。 |
| **`_doc_summary_title_list(forest)`**（内部） | 拼文档级摘要用的标题列表；**单根且子节点多**时附带若干子节点 `title`（便于页驱动下目录可读）。 |

### 5.3 `build_md_pageindex.py`（CLI，`main_argv`）

- 入口：`python build_md_pageindex.py`（模块内 `if __name__ == "__main__"` 调用 `main_argv`）。
- **`--input-md <path>`**（可省略，默认仓库内 `combined_document.md`）、**`--layout`**（**`h1-forest`**（默认）| **`page-per-page`**）、`--out`（可省略，默认 `out_generated.json`）、`--doc-id`、`--if-add-summary`、`--summary-threshold`、**`--page-title-max-chars`**（默认 50，仅 `page_per_page` 下无 `#`/`##` 的页）、`--model`、`--retrieval-ready`。
- 分页导出稿（含 MinerU）：与上相同，使用 **`--layout page-per-page`** 并指定 **`--input-md`**；**不再提供** **`--mineru`** 单独入口。
- 调用 `sync_build_pageindex_payload(md_path, opt, llm_factory=..., layout=...)`，写入 UTF-8 JSON。

### 5.4 `build_md_pageindex.py` 函数文档字符串约定

- **风格**：模块内对外与内部函数（必要时包含 `main_argv` 内的工厂闭包等）的 docstring 统一为**中文**，结构上与 [Google 风格](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings) 一致，便于与现有条目对齐。
- **必含**：
  - **入参**：逐参数说明含义、单位或默认值约定；无参写 **无**。
  - **返回**：说明返回值；无返回值写 **无（`None`）**。
  - **异常**（若存在）：说明可能抛出的异常及触发条件。
- **类型别名**（如 `PageIndexLayout`）：在定义上方使用 **`#:`** 单行说明，或等价注释，替代冗长 docstring。
- **维护要求**：新增或修改函数时须补全或更新上述块，避免仅有一句概述而无入参/返回；与 §5.1～§5.2 函数表不一致时以代码为准并同步表格。

---

## 6. 验收要点

- JSON 根级包含 **`line_count`**、**`summary`**，且 **`result` 中节点在开启摘要时含 `summary`**。
- 不开启摘要时：**`line_count` 仍有**；`summary` 行为按第 4.4 节固定一种策略。
- 与现有 `out.json` 对比：**结构、标题、`page_index`、`text`、`nodes` 一致**；新增字段 **`line_count`、顶层 `summary`、节点 `summary`** 为扩展，不破坏既有消费方时，应以后向兼容方式解析（忽略未知键的客户端仍可工作）。

---

## 7. 修订记录

- **2026-04-16**：在信封与节点 schema 中增加 **`line_count`**（顶层）与 **`summary`**（顶层文档摘要 + 每节点摘要）；补充生成约定与验收要点。
- **2026-04-17**：新增 **§3.1 页驱动 PageIndex**：按 `parse_page_comments` 切段、每页单节点（默认 `nodes: []`，可选页内子树）、文档根 **`title`/`text` 与现行 combined H1 森林下「第一个 H1 段根」规则一致**；分页行归属与现实现一致；明确与 `mineru_canonicalize`/gold 脱钩及测试范围。
- **2026-04-17（修订）**：§3.1 更新为**已实现**说明：`build_md_pageindex` 的 **`layout` / `--layout`**、`build_forest_page_per_page_with_doc_root` 等；**`mineru_canonicalize.py` 已删除**。
- **2026-04-17（§5）**：重写 **§5** 函数表；原 **§5.4** `build_mineru_pageindex` 已废弃。
- **2026-04-17（CLI 合并）**：删除 **`build_mineru_pageindex.py`**；分页稿统一为 **`--input-md` + `--layout page-per-page`**；移除 **`--mineru`**。
- **2026-04-17（CLI）**：**`--md`** 更名为 **`--input-md`**。
- **2026-04-17（§3.1.E）**：无 `#`/`##` 的页节点：LLM 短标题（默认 ≤50 字）或启发式截断；§5.2 表格已更新。
- **2026-04-17（§5.4）**：约定 **`build_md_pageindex.py`** 函数 docstring 须含 **入参 / 返回 / 异常（如有）**；代码侧已补齐未统一处；**`#:`** 用于类型别名说明。
