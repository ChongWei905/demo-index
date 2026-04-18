# `build_md_pageindex.py` 命令行说明

在 **`DemoIndex`** 目录下执行（与 `build_md_pageindex.py` 同级）：

```bash
python build_md_pageindex.py [选项]
```

脚本会从 Markdown 生成 PageIndex 用的 JSON，并写入 `--out` 指定的路径（或下方默认路径）。

---

## 输入与布局

- **`--input-md`**：输入 Markdown 文件路径。  
  **省略** `--input-md` 时，默认使用仓库内  
  `docs/results_with_vlm/game2025report_v7/combined_document.md`。
- **`--layout`**：
  - `h1-forest`（默认）：按一级标题 `#` 分段建树。
  - `page-per-page`：文档根节点 + 每个 `<!-- page:N -->` 逻辑页一个节点（**MinerU 等分页导出稿**用此模式）。每页标题优先页内第一个 `#`，否则第一个 `##`，否则由 LLM/启发式根据正文生成短标题（见 `--page-title-max-chars`）。
- **省略 `--out`** 时，默认写出：  
  `docs/results_with_vlm/game2025report_v7/out_generated.json`。

---

## 常用选项

| 选项 | 说明 |
|------|------|
| `--out` | 输出 JSON 路径（覆盖上述默认）。 |
| `--doc-id` | 写入 JSON 的文档 ID；不填则按输入文件路径自动生成确定性 ID。 |
| `--if-add-summary` | `yes`（默认）或 `no`，是否生成文档级与节点级摘要。 |
| `--summary-threshold` | 摘要相关字符阈值，默认 `600`。 |
| `--page-title-max-chars` | 仅 **`page-per-page`**：某一页没有 `#` 和 `##` 时，用 LLM 生成页标题的最大长度（默认 `50`）；无可用模型时对正文做截断式启发式标题。 |
| `--model` | 调用 LLM 时的模型名（可选，取决于 `llm` 封装）。 |
| `--retrieval-ready` | `yes` 或 `no`（默认），是否标记为检索就绪。 |

---

## 示例

```bash
# 默认：仓库内 combined_document.md → out_generated.json，h1-forest
python build_md_pageindex.py

# 指定合并稿与输出
python build_md_pageindex.py --input-md path/to/combined_document.md --out path/to/out.json

# 分页稿（MinerU 等）：按页建树
python build_md_pageindex.py --input-md path/to/paged_export.md --layout page-per-page --out pageindex.json

# 关闭摘要（节点/文档 summary 为空；若使用 page-per-page 且某页无 #/##，仍会尝试用 LLM 只生成该页标题，除非未配置 llm_factory）
python build_md_pageindex.py --input-md path/to/doc.md --if-add-summary no
```

更完整的设计与字段约定见同目录下的 [`design_md_pageindex.md`](design_md_pageindex.md)。
