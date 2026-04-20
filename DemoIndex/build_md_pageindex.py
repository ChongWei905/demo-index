"""从带 `<!-- page:N -->` 注释与 ATX 标题的合并 Markdown 构建 PageIndex 风格的 JSON。

本模块将全文按一级标题 `#` 切成多棵树的森林，段内用 `##`～`######` 建树；
可选调用 LLM 生成文档级与各节点摘要。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

PAGE_COMMENT_RE = re.compile(r"^\s*<!--\s*page:(\d+)\s*-->\s*$")
ATX_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
PREFIX_QIANYAN_RE = re.compile(r"^前言[：:]\s*(.+)$")


@dataclass
class PageIndexOptions:
    """构建 PageIndex 载荷时的可选项。

    属性:
        doc_id: 输出 JSON 中的文档 ID；为 None 时用文件路径的 UUID5 生成。
        status: 处理状态字符串，写入顶层 ``status``。
        retrieval_ready: 是否写入顶层 ``retrieval_ready``。
        if_add_summary: 为 True 时填充顶层与各节点的 ``summary``（可走 LLM 或启发式）。
        summary_char_threshold: 节点正文短于此字符数时，摘要直接采用正文或标题，不调模型。
        model: 调用 LLM 时使用的模型名；具体含义由 ``llm_factory`` 返回的客户端决定。
        page_title_llm_max_chars: 仅 ``page_per_page`` 布局下，某页无 ``#``/``##`` 时由 LLM
            生成页标题的最大字符数（超出则截断）。
    """

    doc_id: str | None = None
    status: str = "completed"
    retrieval_ready: bool = False
    if_add_summary: bool = True
    summary_char_threshold: int = 600
    model: str | None = None
    page_title_llm_max_chars: int = 50


def compute_line_count(content: str) -> int:
    """统计 Markdown 全文行数（与常见 ``count('\\n') + 1`` 语义一致）。

    入参:
        content: 完整文件文本（UTF-8 解码后的字符串）。

    返回:
        行数，用于顶层 ``line_count`` 字段。
    """
    return content.count("\n") + 1


def parse_page_comments(lines: list[str]) -> list[int]:
    """按行解析 ``<!-- page:N -->``，得到每一行「行首」所处的逻辑页码。

    入参:
        lines: 按行切分后的文本行列表（不含换行符）。

    返回:
        与 ``lines`` 等长的整数列表；``page_by_line[i]`` 表示第 ``i`` 行开始时生效的页码
        （本行若为页码注释，先记入当前页，再在本行末尾更新后续页码）。
    """
    current = 1
    out: list[int] = []
    for line in lines:
        out.append(current)
        m = PAGE_COMMENT_RE.match(line)
        if m:
            current = int(m.group(1))
    return out


def iter_atx_headers(lines: list[str]) -> list[dict[str, Any]]:
    """扫描 ATX 标题（``#``～``######``），跳过围栏代码块内的行。

    入参:
        lines: 按行切分后的文本行列表。

    返回:
        标题字典列表，每项含 ``line_idx``（0 起始行号）、``level``（1～6）、
        ``raw_title``（去掉 ``#`` 后的标题原文）。
    """
    headers: list[dict[str, Any]] = []
    in_code = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = ATX_HEADER_RE.match(line.rstrip("\n"))
        if m:
            level = len(m.group(1))
            raw = m.group(2).strip()
            headers.append({"line_idx": i, "level": level, "raw_title": raw})
    return headers


def find_h1_line_indices(headers: list[dict[str, Any]]) -> list[int]:
    """从标题列表中取出所有一级标题所在行号。

    入参:
        headers: ``iter_atx_headers`` 的返回值。

    返回:
        每个 ``#``（且非 ``##``）标题的 ``line_idx``，按文档顺序排列。
    """
    return [h["line_idx"] for h in headers if h["level"] == 1]


def normalize_display_title(raw_title: str, level: int) -> str:
    """将 Markdown 标题转为对外展示的 ``title``（如去掉「前言：」、压缩「2025 年」）。

    入参:
        raw_title: 标题行去掉 ``#`` 后的字符串。
        level: 标题层级 1～6。

    返回:
        规范化后的展示标题。
    """
    t = raw_title.strip()
    if level >= 2:
        m = PREFIX_QIANYAN_RE.match(t)
        if m:
            t = m.group(1).strip()
    t = re.sub(r"(\d)\s+年", r"\1年", t)
    return t


def _normalize_title_for_matching(title: str) -> str:
    """将标题文本规范化用于模糊匹配（去除空格、标点、全半角差异）。

    入参:
        title: 原始标题文本。

    返回:
        规范化后的小写字符串，用于匹配比较。
    """
    s = title.strip()
    s = re.sub(r"\s+", "", s)
    s = s.replace("：", ":").replace("；", ";").replace("，", ",").replace("。", ".")
    s = s.replace("（", "(").replace("）", ")").replace("【", "[").replace("】", "]")
    s = s.replace("—", "-").replace("–", "-").replace("…", "...")
    s = s.replace(chr(0x201c), chr(34)).replace(chr(0x201d), chr(34)).replace(chr(0x2018), chr(39)).replace(chr(0x2019), chr(39))
    return s.lower()


def parse_toc_file(toc_path: str | Path) -> list[dict[str, Any]]:
    """解析 TOC JSON 文件，返回 ``(title, level)`` 有序列表。

    支持两种格式：嵌套树形（含 ``children`` 键）和扁平列表（含 ``level`` 键）。
    自动检测：若首项含 ``children`` 键则按嵌套树形解析，否则按扁平列表解析。

    入参:
        toc_path: TOC JSON 文件路径。

    返回:
        有序字典列表，每项含 ``title``（str）和 ``level``（int）。

    异常:
        FileNotFoundError: 文件不存在。
        ValueError: JSON 格式无效或内容为空。
    """
    path = Path(toc_path)
    if not path.exists():
        raise FileNotFoundError(f"TOC file not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list) or not data:
        raise ValueError("TOC file must contain a non-empty JSON array.")
    if "children" in data[0]:
        return _parse_toc_nested(data)
    return _parse_toc_flat(data)


def _parse_toc_nested(items: list[dict[str, Any]], depth: int = 1) -> list[dict[str, Any]]:
    """递归解析嵌套树形 TOC 格式（内部）。

    入参:
        items: 当前层级的 TOC 条目列表。
        depth: 当前嵌套深度（从 1 开始）。

    返回:
        有序字典列表，每项含 ``title`` 和 ``level``。
    """
    result: list[dict[str, Any]] = []
    for item in items:
        title = str(item.get("title", "")).strip()
        if title:
            result.append({"title": title, "level": depth})
        children = item.get("children") or []
        if children:
            result.extend(_parse_toc_nested(children, depth + 1))
    return result


def _parse_toc_flat(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """解析扁平列表 TOC 格式（内部）。

    入参:
        items: TOC 条目列表，每项含 ``title`` 和 ``level``。

    返回:
        有序字典列表，每项含 ``title`` 和 ``level``。
    """
    result: list[dict[str, Any]] = []
    for item in items:
        title = str(item.get("title", "")).strip()
        level = int(item.get("level", 1))
        if title:
            result.append({"title": title, "level": max(1, min(level, 6))})
    return result


def match_toc_to_headers(
    toc_entries: list[dict[str, Any]],
    headers: list[dict[str, Any]],
) -> list[int | None]:
    """将 TOC 条目与 MD 标题列表做模糊匹配，返回每个 MD 标题对应的 TOC level。

    匹配规则（§3.2.B）：
    1. 双方标题经 ``_normalize_title_for_matching`` 规范化后比较。
    2. TOC 标题为 MD 标题的子串，或反之，均视为匹配。
    3. 匹配优先级：精确匹配 > TOC 包含 MD > MD 包含 TOC。
    4. 每个 TOC 条目最多匹配一个 MD 标题（贪心，按文档顺序）。

    入参:
        toc_entries: ``parse_toc_file`` 返回的 TOC 条目列表。
        headers: ``iter_atx_headers`` 返回的标题列表。

    返回:
        与 ``headers`` 等长的列表；匹配成功时为 TOC level（int），未匹配为 None。
    """
    matched_toc_indices: set[int] = set()
    result: list[int | None] = [None] * len(headers)

    normalized_toc = [_normalize_title_for_matching(e["title"]) for e in toc_entries]
    normalized_headers = [_normalize_title_for_matching(h["raw_title"]) for h in headers]

    for h_idx, nh in enumerate(normalized_headers):
        best_toc_idx: int | None = None
        best_priority: int = -1
        for t_idx, nt in enumerate(normalized_toc):
            if t_idx in matched_toc_indices:
                continue
            if nh == nt:
                priority = 3
            elif nt and nh and nt in nh:
                priority = 2
            elif nt and nh and nh in nt:
                priority = 1
            else:
                continue
            if priority > best_priority:
                best_priority = priority
                best_toc_idx = t_idx
        if best_toc_idx is not None:
            matched_toc_indices.add(best_toc_idx)
            result[h_idx] = toc_entries[best_toc_idx]["level"]

    return result


def _auto_normalize_levels(headers: list[dict[str, Any]]) -> None:
    """自动层级规范化（§3.2.D）：消除跳跃，保证 level[i] <= max(level[0..i-1]) + 1。

    额外规则：若规范化后所有 level 均 >= 2（无 H1），则整体减 1。

    入参:
        headers: 标题列表；原地修改 ``level`` 字段。

    返回:
        无（``None``）。
    """
    if not headers:
        return
    max_allowed = 1
    for h in headers:
        level = h["level"]
        if level > max_allowed + 1:
            level = max_allowed + 1
        if level < 1:
            level = 1
        if level > 6:
            level = 6
        h["level"] = level
        max_allowed = max(max_allowed, level)
    if all(h["level"] >= 2 for h in headers):
        for h in headers:
            h["level"] -= 1


def normalize_header_levels(
    headers: list[dict[str, Any]],
    *,
    toc_entries: list[dict[str, Any]] | None = None,
    normalize_levels: bool = True,
) -> list[dict[str, Any]]:
    """层级调整主入口（§3.2.C + §3.2.D）。

    若提供 ``toc_entries`` 则先执行 TOC 驱动覆写；若 ``normalize_levels`` 为 True 则再执行
    自动规范化。返回调整后的标题列表（原地修改 ``level`` 字段）。

    入参:
        headers: ``iter_atx_headers`` 返回的标题列表；原地修改。
        toc_entries: ``parse_toc_file`` 返回的 TOC 条目列表；为 None 时跳过 TOC 覆写。
        normalize_levels: 是否执行自动层级规范化（默认 True）。

    返回:
        调整后的标题列表（与输入为同一对象）。
    """
    if toc_entries:
        toc_levels = match_toc_to_headers(toc_entries, headers)
        for h_idx, toc_level in enumerate(toc_levels):
            if toc_level is not None:
                headers[h_idx]["level"] = toc_level
    if normalize_levels:
        _auto_normalize_levels(headers)
    return headers


def _join_lines(lines: list[str], start: int, end: int) -> str:
    """将 ``lines[start:end+1]`` 用换行拼接为一段文本，并去掉首尾多余换行。

    入参:
        lines: 全文行列表。
        start: 起始行下标（含）。
        end: 结束行下标（含）。

    返回:
        拼接后的字符串。
    """
    chunk = lines[start : end + 1]
    return "\n".join(chunk).strip("\n")


def _headers_in_range(
    all_headers: list[dict[str, Any]], start: int, end: int, min_level: int = 2
) -> list[dict[str, Any]]:
    """筛选落在闭区间 ``[start, end]`` 内、且层级 ``>= min_level`` 的标题。

    入参:
        all_headers: 全文标题列表。
        start: 起始行下标（含）。
        end: 结束行下标（含）。
        min_level: 最小标题层级，默认 2（即段内从 ``##`` 起）。

    返回:
        满足条件的标题字典子列表，顺序与文中出现顺序一致。
    """
    return [h for h in all_headers if start <= h["line_idx"] <= end and h["level"] >= min_level]


def build_section_root_and_flat_nodes(
    lines: list[str],
    page_by_line: list[int],
    all_headers: list[dict[str, Any]],
    h1_line: int,
    section_end: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """为单个 H1 段构建根节点（无 ``node_id`` / 子树）与段内 ``##+`` 的扁平节点列表。

    根节点正文为 H1 行起至第一个 ``##`` 之前；各子标题正文至「段内下一个任意级标题」之前。

    入参:
        lines: 全文行列表。
        page_by_line: ``parse_page_comments`` 返回值。
        all_headers: ``iter_atx_headers`` 返回值。
        h1_line: 当前段 H1 标题行的 0 起始下标。
        section_end: 当前段最后一行的 0 起始下标（含）。

    返回:
        ``(root, flat)``：``root`` 含 ``_line_idx``、``title``、``page_index``、``text``；
        ``flat`` 为段内二级及以下标题的扁平列表，每项含 ``_line_idx``、``level``、
        ``title``、``page_index``、``text``。
    """
    h1_header = next(h for h in all_headers if h["line_idx"] == h1_line and h["level"] == 1)
    raw_h1 = h1_header["raw_title"]
    title = normalize_display_title(raw_h1, 1)
    subs = _headers_in_range(all_headers, h1_line + 1, section_end, min_level=2)

    if not subs:
        text = _join_lines(lines, h1_line, section_end)
        root = {
            "_line_idx": h1_line,
            "title": title,
            "page_index": page_by_line[h1_line],
            "text": text,
        }
        return root, []

    first_sub_line = subs[0]["line_idx"]
    root_text = _join_lines(lines, h1_line, first_sub_line - 1)
    root = {
        "_line_idx": h1_line,
        "title": title,
        "page_index": page_by_line[h1_line],
        "text": root_text,
    }

    # Each subsection's text runs until the immediately following header in this section
    # (any level). This matches VLM PageIndex samples: parent holds only intro before
    # the first child heading; deeper content lives under child nodes.
    flat: list[dict[str, Any]] = []
    for j, h in enumerate(subs):
        lvl = h["level"]
        line_i = h["line_idx"]
        if j + 1 < len(subs):
            next_boundary = subs[j + 1]["line_idx"]
        else:
            next_boundary = section_end + 1
        body = _join_lines(lines, line_i, next_boundary - 1)
        flat.append(
            {
                "_line_idx": line_i,
                "level": lvl,
                "title": normalize_display_title(h["raw_title"], lvl),
                "page_index": page_by_line[line_i],
                "text": body,
            }
        )
    return root, flat


def build_tree_from_flat_nodes(flat: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将按文档顺序排列的扁平节点（含 ``level``）用栈算法挂成若干子树根列表。

    入参:
        flat: ``build_section_root_and_flat_nodes`` 返回的扁平列表。

    返回:
        森林中「仅子树部分」的根节点列表；每项含 ``_line_idx``、``title``、``page_index``、
        ``text``、``nodes``（可能嵌套）。
    """
    if not flat:
        return []
    stack: list[tuple[dict[str, Any], int]] = []
    roots: list[dict[str, Any]] = []
    for node in flat:
        tree_node: dict[str, Any] = {
            "_line_idx": node["_line_idx"],
            "title": node["title"],
            "page_index": node["page_index"],
            "text": node["text"],
            "nodes": [],
        }
        lvl = node["level"]
        while stack and stack[-1][1] >= lvl:
            stack.pop()
        if not stack:
            roots.append(tree_node)
        else:
            stack[-1][0]["nodes"].append(tree_node)
        stack.append((tree_node, lvl))
    return roots


def _merge_root_and_children(root: dict[str, Any], child_trees: list[dict[str, Any]]) -> dict[str, Any]:
    """把 H1 根字典与段内子树列表合并为一棵完整树节点。

    入参:
        root: 含 ``_line_idx``、``title``、``page_index``、``text`` 的根片段。
        child_trees: ``build_tree_from_flat_nodes`` 的返回值。

    返回:
        带 ``nodes`` 字段的完整树节点（仍含内部字段 ``_line_idx``）。
    """
    out = {
        "_line_idx": root["_line_idx"],
        "title": root["title"],
        "page_index": root["page_index"],
        "text": root["text"],
        "nodes": child_trees,
    }
    return out


def build_forest_from_markdown(
    lines: list[str],
    page_by_line: list[int],
    *,
    toc_entries: list[dict[str, Any]] | None = None,
    normalize_levels: bool = True,
) -> list[dict[str, Any]]:
    """根据全文行与页码映射，按每个 H1 切段并建树，得到多棵树的列表。

    入参:
        lines: 全文行列表。
        page_by_line: ``parse_page_comments`` 返回值。
        toc_entries: ``parse_toc_file`` 返回的 TOC 条目列表；为 None 时跳过 TOC 覆写。
        normalize_levels: 是否执行自动层级规范化（默认 True）。

    返回:
        森林：每个元素是一棵 H1 为根的树（含 ``_line_idx``、``title``、``page_index``、
        ``text``、``nodes``），尚未分配 ``node_id``。

    异常:
        ValueError: 文中没有 H1 标题时抛出。
    """
    all_h = iter_atx_headers(lines)
    normalize_header_levels(all_h, toc_entries=toc_entries, normalize_levels=normalize_levels)
    h1s = find_h1_line_indices(all_h)
    if not h1s:
        raise ValueError("No H1 (#) headings found in markdown.")
    forest: list[dict[str, Any]] = []
    for i, h1_line in enumerate(h1s):
        section_end = (h1s[i + 1] - 1) if i + 1 < len(h1s) else (len(lines) - 1)
        root, flat = build_section_root_and_flat_nodes(lines, page_by_line, all_h, h1_line, section_end)
        child_trees = build_tree_from_flat_nodes(flat)
        forest.append(_merge_root_and_children(root, child_trees))
    return forest


#: 构建树时的布局模式，用作 ``layout`` 参数取值：``h1_forest`` | ``page_per_page``。
PageIndexLayout = Literal["h1_forest", "page_per_page"]


def group_line_ranges_by_page(page_by_line: list[int]) -> list[tuple[int, int, int]]:
    """将 ``parse_page_comments`` 结果合并为连续行区间。

    入参:
        page_by_line: 与全文行等长的页码列表，通常来自 ``parse_page_comments(lines)``。

    返回:
        ``(page_num, start, end)`` 元组列表；``start``/``end`` 为 0 起始、闭区间行下标。
    """
    if not page_by_line:
        return []
    out: list[tuple[int, int, int]] = []
    start = 0
    p = page_by_line[0]
    for i in range(1, len(page_by_line)):
        if page_by_line[i] != p:
            out.append((p, start, i - 1))
            start = i
            p = page_by_line[i]
    out.append((p, start, len(page_by_line) - 1))
    return out


def _page_node_title(
    lines: list[str],
    start: int,
    end: int,
    page_num: int,
    all_headers: list[dict[str, Any]],
) -> tuple[str, bool]:
    """计算「每页一节点」布局下该页的展示标题（内部）。

    入参:
        lines: 全文行列表（当前仅用于签名一致性，标题由 ``all_headers`` 与区间决定）。
        start: 该页起始行下标（含）。
        end: 该页结束行下标（含）。
        page_num: 逻辑页码 ``N``（用于占位及启发式回退）。
        all_headers: ``iter_atx_headers(lines)`` 的返回值。

    返回:
        ``(title, needs_llm_title)``：页内首个 ``#`` 或首个 ``##`` 的规范化标题时第二项为
        False；二者皆无时第一项为临时占位 ``第 {page_num} 页``，第二项为 True（后续由
        LLM 或启发式替换为短标题，见设计文档 §3.1.E）。
    """
    h1 = next(
        (h for h in all_headers if h["level"] == 1 and start <= h["line_idx"] <= end),
        None,
    )
    if h1:
        return (normalize_display_title(h1["raw_title"], 1), False)
    h2 = next(
        (h for h in all_headers if h["level"] == 2 and start <= h["line_idx"] <= end),
        None,
    )
    if h2:
        return (normalize_display_title(h2["raw_title"], 2), False)
    return (f"第 {page_num} 页", True)


def build_forest_page_per_page_with_doc_root(
    lines: list[str],
    page_by_line: list[int],
    *,
    toc_entries: list[dict[str, Any]] | None = None,
    normalize_levels: bool = True,
) -> list[dict[str, Any]]:
    """页驱动布局：单文档根 + 每页一个子节点（整页 ``text``，子节点 ``nodes`` 为空列表）。

    文档根与全篇第一个 H1 段的根规则一致（``build_section_root_and_flat_nodes``）；
    详见 ``docs/design/design_md_pageindex.md`` §3.1。

    入参:
        lines: 全文行列表。
        page_by_line: 与 ``lines`` 等长的页码列表，来自 ``parse_page_comments(lines)``。
        toc_entries: ``parse_toc_file`` 返回的 TOC 条目列表；为 None 时跳过 TOC 覆写。
        normalize_levels: 是否执行自动层级规范化（默认 True）。

    返回:
        长度为 1 的森林列表，元素为文档根字典（含 ``_line_idx``、``title``、``page_index``、
        ``text``、``nodes``）；``nodes`` 为按页顺序的页节点列表。

    异常:
        ValueError: ``lines`` 与 ``page_by_line`` 长度不一致时抛出。
    """
    if len(lines) != len(page_by_line):
        raise ValueError("lines and page_by_line must have the same length.")
    all_h = iter_atx_headers(lines)
    normalize_header_levels(all_h, toc_entries=toc_entries, normalize_levels=normalize_levels)
    h1s = find_h1_line_indices(all_h)

    if h1s:
        h1_line = h1s[0]
        section_end = (h1s[1] - 1) if len(h1s) > 1 else (len(lines) - 1)
        root, _flat = build_section_root_and_flat_nodes(
            lines, page_by_line, all_h, h1_line, section_end
        )
        doc_root: dict[str, Any] = {
            "_line_idx": root["_line_idx"],
            "title": root["title"],
            "page_index": root["page_index"],
            "text": root["text"],
            "nodes": [],
        }
    else:
        doc_root = {
            "_line_idx": 0,
            "title": "Document",
            "page_index": page_by_line[0] if page_by_line else 1,
            "text": "",
            "nodes": [],
        }

    page_children: list[dict[str, Any]] = []
    for page_num, start, end in group_line_ranges_by_page(page_by_line):
        title, needs_llm_title = _page_node_title(lines, start, end, page_num, all_h)
        node: dict[str, Any] = {
            "_line_idx": start,
            "title": title,
            "page_index": page_num,
            "text": _join_lines(lines, start, end),
            "nodes": [],
        }
        if needs_llm_title:
            node["_needs_llm_page_title"] = True
        page_children.append(node)
    doc_root["nodes"] = page_children
    return [doc_root]


def assign_node_ids_preorder(forest: list[dict[str, Any]]) -> None:
    """对整片森林做深度优先先序遍历，依次为节点写入 ``node_id``（``0000``、``0001``、…）。

    入参:
        forest: 内存中的树森林，例如 ``build_forest_from_markdown`` 或
        ``build_forest_page_per_page_with_doc_root`` 的返回值；**原地**修改各节点。

    返回:
        无（``None``）。
    """
    counter = 0

    def visit(n: dict[str, Any]) -> None:
        """递归访问单节点并分配递增 ``node_id``。

        入参:
            n: 当前树节点；原地写入 ``node_id``。

        返回:
            无。
        """
        nonlocal counter
        n["node_id"] = str(counter).zfill(4)
        counter += 1
        for c in n.get("nodes") or []:
            visit(c)

    for root in forest:
        visit(root)


def strip_internal_fields(tree: dict[str, Any]) -> dict[str, Any]:
    """去掉内部字段 ``_line_idx``，保留对外 JSON 所需字段（含 ``summary``）。

    入参:
        tree: 已含 ``node_id``、``summary`` 等字段的树节点。

    返回:
        新字典，键为 ``title``、``node_id``、``page_index``、``text``、``summary``；
        若有非空子节点则含 ``nodes``。
    """
    out: dict[str, Any] = {
        "title": tree["title"],
        "node_id": tree["node_id"],
        "page_index": tree["page_index"],
        "text": tree["text"],
        "summary": tree.get("summary", ""),
    }
    nodes = tree.get("nodes") or []
    if nodes:
        out["nodes"] = [strip_internal_fields(ch) for ch in nodes]
    return out


def strip_forest(forest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对森林中每棵树调用 ``strip_internal_fields``。

    入参:
        forest: 内存中的树列表。

    返回:
        用于序列化到 ``result`` 字段的树列表副本。
    """
    return [strip_internal_fields(t) for t in forest]


def _doc_summary_title_list(forest: list[dict[str, Any]]) -> list[str]:
    """组装文档级摘要（启发式或 LLM）所用的章节标题列表（内部）。

    入参:
        forest: 内存树森林；若仅含一棵根且其 ``nodes`` 非空，则拼接根标题与至多 25 个子节点
        标题，否则只取各树根的 ``title``。

    返回:
        非空的标题字符串列表，供 ``_heuristic_doc_summary`` / ``generate_doc_summary`` 使用。
    """
    if len(forest) == 1:
        root = forest[0]
        kids = root.get("nodes") or []
        if kids:
            return [root["title"]] + [k["title"] for k in kids[:25]]
        return [root["title"]]
    return [t["title"] for t in forest]


def _heuristic_doc_summary(forest: list[dict[str, Any]], line_count: int) -> str:
    """无 LLM 时，用行数与 ``_doc_summary_title_list`` 拼一段简短文档摘要。

    入参:
        forest: 内存中的树森林。
        line_count: 全文行数，写入摘要句首。

    返回:
        一段中文说明性字符串（标题至多取前 12 条，超出加「等」）。
    """
    titles = _doc_summary_title_list(forest)
    return (
        f"本文档共 {line_count} 行，包含以下主要部分："
        + "、".join(titles[:12])
        + ("等。" if len(titles) > 12 else "。")
    )


async def generate_doc_summary(
    forest: list[dict[str, Any]],
    full_text_sample: str,
    line_count: int,
    *,
    model: str | None,
    llm: Any | None,
    use_llm: bool,
) -> str:
    """生成文档级摘要（顶层 ``summary``）。

    入参:
        forest: 内存树森林，用于提取章节标题列表。
        full_text_sample: 全文或摘录，供 LLM 提示使用（会截断）。
        line_count: 全文行数；无 LLM 时参与启发式摘要。
        model: LLM 模型名。
        llm: 具备 ``acompletion(model, prompt) -> str`` 的异步客户端；可为 None。
        use_llm: 为 True 且 ``llm`` 非空时走模型，否则走 ``_heuristic_doc_summary``。

    返回:
        文档级摘要字符串。
    """
    if not use_llm or llm is None:
        return _heuristic_doc_summary(forest, line_count)
    titles = _doc_summary_title_list(forest)
    prompt = (
        "请用 2～4 句中文概括以下报告的目的、主要章节要点和结论导向。"
        "不要编造数据，仅根据给出的目录与摘录推断。\n\n"
        f"主要章节标题：{json.dumps(titles, ensure_ascii=False)}\n\n"
        f"正文摘录（可能截断）：\n{full_text_sample[:8000]}"
    )
    return (await llm.acompletion(model=model, prompt=prompt)).strip()


def _heuristic_node_summary(title: str, text: str, max_len: int = 320) -> str:
    """无 LLM 时，将正文压成短摘要（过长则截断加省略号）。

    入参:
        title: 节点标题（正文为空时可作回退）。
        text: 节点正文。
        max_len: 摘要最大字符数。

    返回:
        启发式摘要字符串。
    """
    body = text.replace("\n", " ").strip()
    if len(body) <= max_len:
        return body if body else title
    return (body[: max_len - 1] + "…").strip()


def _collect_nodes_needing_llm_page_title(forest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """收集仍带 ``_needs_llm_page_title`` 标记的树节点（内部）。

    入参:
        forest: 内存树森林。

    返回:
        需补全页标题的节点列表（通常为 ``page_per_page`` 下无 ``#``/``##`` 的页节点）。
    """
    out: list[dict[str, Any]] = []

    def walk(n: dict[str, Any]) -> None:
        if n.get("_needs_llm_page_title"):
            out.append(n)
        for c in n.get("nodes") or []:
            walk(c)

    for r in forest:
        walk(r)
    return out


def _heuristic_short_page_title(text: str, page_num: int, max_len: int) -> str:
    """无 LLM 时，将页正文压成不超过 ``max_len`` 字的短标题。

    入参:
        text: 该页 ``text``。
        page_num: 逻辑页码；正文为空时写入 ``第 {page_num} 页``。
        max_len: 最大字符数。

    返回:
        非空标题字符串。
    """
    body = text.replace("\n", " ").strip()
    if not body:
        return f"第 {page_num} 页"
    if len(body) <= max_len:
        return body
    return (body[: max_len - 1] + "…").strip()


def _sanitize_llm_page_title_line(raw: str, max_len: int) -> str:
    """取模型输出首行并截断到 ``max_len``，去掉常见前缀与首尾引号。

    入参:
        raw: 模型原始输出。
        max_len: 标题最大长度。

    返回:
        清理后的标题；无效时为空串。
    """
    s = raw.split("\n")[0].strip().strip('"').strip("'")
    for prefix in ("标题：", "标题:", "Title:", "输出：", "输出:"):
        if s.startswith(prefix):
            s = s[len(prefix) :].strip()
    if not s:
        return ""
    if len(s) > max_len:
        return s[:max_len].rstrip() + "…"
    return s


async def generate_llm_page_titles_when_no_heading(
    forest: list[dict[str, Any]],
    *,
    model: str | None,
    llm: Any | None,
    use_llm: bool,
    max_title_chars: int,
) -> None:
    """为无 ``#``/``##`` 的页节点写入最终 ``title``（LLM 一句概括或启发式）。

    原地修改 ``forest`` 中带 ``_needs_llm_page_title`` 的节点，并移除该标记。

    入参:
        forest: 内存树森林。
        model: LLM 模型名。
        llm: 异步客户端；``use_llm`` 为 True 且非空时调用 ``acompletion``。
        use_llm: 是否调用模型。
        max_title_chars: 标题最大字符数（提示与截断一致）。

    返回:
        无（``None``）。
    """
    nodes = _collect_nodes_needing_llm_page_title(forest)
    if not nodes:
        return

    async def one(n: dict[str, Any]) -> None:
        text = (n.get("text") or "").strip()
        page_num = int(n.get("page_index") or 0)
        if not use_llm or llm is None:
            n["title"] = _heuristic_short_page_title(text, page_num, max_title_chars)
            n.pop("_needs_llm_page_title", None)
            return
        prompt = (
            f"请根据下面书页/幻灯的正文，生成一个简短中文标题，不得超过{max_title_chars}个字符。"
            "只输出标题本身，不要引号、书名号、序号或任何解释。\n\n"
            f"正文：\n{text[:12000]}"
        )
        try:
            raw = (await llm.acompletion(model=model, prompt=prompt)).strip()
        except Exception:
            raw = ""
        title = _sanitize_llm_page_title_line(raw, max_title_chars)
        if not title:
            title = _heuristic_short_page_title(text, page_num, max_title_chars)
        n["title"] = title
        n.pop("_needs_llm_page_title", None)

    await asyncio.gather(*[one(n) for n in nodes])


async def generate_node_summaries(
    forest: list[dict[str, Any]],
    *,
    model: str | None,
    llm: Any | None,
    use_llm: bool,
    char_threshold: int,
) -> None:
    """为森林中每个节点原地写入 ``summary``（并发异步调用 LLM 或启发式）。

    入参:
        forest: 内存树森林；原地修改。
        model: LLM 模型名。
        llm: 异步补全客户端；可为 None。
        use_llm: 是否调用模型（否则用截断/全文作摘要）。
        char_threshold: 正文短于此长度则摘要不调模型，直接采用正文或标题。

    返回:
        无（``None``）。
    """
    nodes_flat: list[dict[str, Any]] = []

    def collect(n: dict[str, Any]) -> None:
        """先序遍历，将节点及其子孙加入 ``nodes_flat``。

        入参:
            n: 树节点。

        返回:
            无。
        """
        nodes_flat.append(n)
        for c in n.get("nodes") or []:
            collect(c)

    for r in forest:
        collect(r)

    async def one(n: dict[str, Any]) -> None:
        """为单个节点生成 ``summary``（短正文直拷贝，长正文启发式或 LLM）。

        入参:
            n: 树节点；原地写入 ``summary``。

        返回:
            无。
        """
        text = n.get("text") or ""
        title = n.get("title") or ""
        if len(text) < char_threshold:
            n["summary"] = text if text else title
            return
        if not use_llm or llm is None:
            n["summary"] = _heuristic_node_summary(title, text)
            return
        prompt = (
            "用1～3 句中文概括下面小节内容，保留关键术语与数字；不要添加小节中没有的信息。\n"
            f"标题：{title}\n\n正文：\n{text[:12000]}"
        )
        n["summary"] = (await llm.acompletion(model=model, prompt=prompt)).strip()

    await asyncio.gather(*[one(n) for n in nodes_flat])


async def build_pageindex_payload(
    md_path: str | Path,
    opt: PageIndexOptions | None = None,
    llm_factory: Callable[[], Any] | None = None,
    *,
    layout: PageIndexLayout = "h1_forest",
    toc_file: str | Path | None = None,
    normalize_levels: bool = True,
) -> dict[str, Any]:
    """从 Markdown 文件路径构建完整 API 载荷（含 ``doc_id``、``line_count``、``summary``、``result``）。

    入参:
        md_path: ``combined_document.md`` 等文件路径。
        opt: 选项；默认 ``PageIndexOptions()``。
        llm_factory: 无参可调用，返回 LLM 客户端；为 None 或创建失败时摘要走启发式/空串。
        layout: ``h1_forest`` 按一级标题分段；``page_per_page`` 为文档根 + 每页一节点（§3.1）。
        toc_file: TOC JSON 文件路径；为 None 时跳过 TOC 覆写。
        normalize_levels: 是否执行自动层级规范化（默认 True）。

    返回:
        可 ``json.dumps`` 的字典，键含 ``doc_id``、``status``、``retrieval_ready``、
        ``line_count``、``summary``、``result``。
    """
    opt = opt or PageIndexOptions()
    path = Path(md_path)
    content = path.read_text(encoding="utf-8")
    lines = content.split("\n")
    line_count = compute_line_count(content)
    page_by_line = parse_page_comments(lines)

    toc_entries = parse_toc_file(toc_file) if toc_file else None
    forest = _build_forest_for_layout(
        lines, page_by_line, layout, toc_entries=toc_entries, normalize_levels=normalize_levels
    )
    assign_node_ids_preorder(forest)

    needs_llm_page_title = bool(_collect_nodes_needing_llm_page_title(forest))
    llm = None
    want_llm = (opt.if_add_summary or needs_llm_page_title) and llm_factory is not None
    if want_llm:
        try:
            llm = llm_factory()
        except Exception:
            llm = None

    use_llm_summaries = opt.if_add_summary and llm is not None
    use_llm_page_titles = needs_llm_page_title and llm is not None

    if needs_llm_page_title:
        await generate_llm_page_titles_when_no_heading(
            forest,
            model=opt.model,
            llm=llm,
            use_llm=use_llm_page_titles,
            max_title_chars=opt.page_title_llm_max_chars,
        )

    if opt.if_add_summary:
        await generate_node_summaries(
            forest,
            model=opt.model,
            llm=llm,
            use_llm=use_llm_summaries,
            char_threshold=opt.summary_char_threshold,
        )
        doc_summary = await generate_doc_summary(
            forest,
            content,
            line_count,
            model=opt.model,
            llm=llm,
            use_llm=use_llm_summaries,
        )
    else:
        for r in forest:
            _clear_summaries(r)
        doc_summary = ""

    doc_id = opt.doc_id
    if not doc_id:
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(path.resolve())))

    result_trees = strip_forest(forest)
    return {
        "doc_id": doc_id,
        "status": opt.status,
        "retrieval_ready": opt.retrieval_ready,
        "line_count": line_count,
        "summary": doc_summary,
        "result": result_trees,
    }


def _build_forest_for_layout(
    lines: list[str],
    page_by_line: list[int],
    layout: PageIndexLayout,
    *,
    toc_entries: list[dict[str, Any]] | None = None,
    normalize_levels: bool = True,
) -> list[dict[str, Any]]:
    """按布局枚举选择建树实现（内部）。

    入参:
        lines: 全文行列表。
        page_by_line: ``parse_page_comments(lines)`` 的返回值。
        layout: ``h1_forest`` 或 ``page_per_page``。
        toc_entries: ``parse_toc_file`` 返回的 TOC 条目列表。
        normalize_levels: 是否执行自动层级规范化。

    返回:
        未分配 ``node_id`` 的树森林。

    异常:
        ValueError: 当 ``layout`` 为 ``h1_forest`` 且文中无 H1 时，由
        ``build_forest_from_markdown`` 抛出。
    """
    if layout == "page_per_page":
        return build_forest_page_per_page_with_doc_root(
            lines, page_by_line, toc_entries=toc_entries, normalize_levels=normalize_levels
        )
    return build_forest_from_markdown(
        lines, page_by_line, toc_entries=toc_entries, normalize_levels=normalize_levels
    )


def _clear_summaries(n: dict[str, Any]) -> None:
    """递归将节点 ``summary`` 置为空串（关闭摘要生成时使用）。

    入参:
        n: 树节点；原地修改。

    返回:
        无（``None``）。
    """
    n["summary"] = ""
    for c in n.get("nodes") or []:
        _clear_summaries(c)


def sync_build_pageindex_payload(
    md_path: str | Path,
    opt: PageIndexOptions | None = None,
    llm_factory: Callable[[], Any] | None = None,
    *,
    layout: PageIndexLayout = "h1_forest",
    toc_file: str | Path | None = None,
    normalize_levels: bool = True,
) -> dict[str, Any]:
    """``build_pageindex_payload`` 的同步包装，在内部 ``asyncio.run`` 一次。

    入参:
        md_path: Markdown 文件路径。
        opt: 构建选项。
        llm_factory: LLM 工厂，语义同 ``build_pageindex_payload``。
        layout: 布局，语义同 ``build_pageindex_payload``。
        toc_file: TOC JSON 文件路径；为 None 时跳过 TOC 覆写。
        normalize_levels: 是否执行自动层级规范化（默认 True）。

    返回:
        与 ``build_pageindex_payload`` 相同的字典。
    """
    return asyncio.run(
        build_pageindex_payload(
            md_path, opt, llm_factory, layout=layout, toc_file=toc_file,
            normalize_levels=normalize_levels
        )
    )


async def build_pageindex_payload_from_lines(
    lines: list[str],
    opt: PageIndexOptions | None = None,
    llm_factory: Callable[[], Any] | None = None,
    *,
    doc_id_seed: str | None = None,
    layout: PageIndexLayout = "h1_forest",
    toc_entries: list[dict[str, Any]] | None = None,
    normalize_levels: bool = True,
) -> dict[str, Any]:
    """从行列表构建完整 PageIndex API 载荷（无文件路径，便于管道内调用）。

    入参:
        lines: 已按换行切分后的行列表（每行不含换行符）。
        opt: 选项；默认 ``PageIndexOptions()``。
        llm_factory: 无参可调用，返回 LLM 客户端；为 None 或创建失败时摘要走启发式。
        doc_id_seed: 当 ``opt.doc_id`` 为 None 时，参与 UUID5 的种子（默认取内容前 8000 字符）。
        layout: ``h1_forest`` 或 ``page_per_page``。
        toc_entries: ``parse_toc_file`` 返回的 TOC 条目列表；为 None 时跳过 TOC 覆写。
        normalize_levels: 是否执行自动层级规范化（默认 True）。

    返回:
        与 ``build_pageindex_payload`` 相同结构的字典。

    异常:
        ValueError: 当 ``layout`` 为 ``h1_forest`` 且文中无 H1 时可能抛出。
    """
    opt = opt or PageIndexOptions()
    content = "\n".join(lines)
    if not content.endswith("\n"):
        content += "\n"
    line_count = compute_line_count(content)
    page_by_line = parse_page_comments(lines)
    forest = _build_forest_for_layout(
        lines, page_by_line, layout, toc_entries=toc_entries, normalize_levels=normalize_levels
    )
    assign_node_ids_preorder(forest)

    needs_llm_page_title = bool(_collect_nodes_needing_llm_page_title(forest))
    llm = None
    want_llm = (opt.if_add_summary or needs_llm_page_title) and llm_factory is not None
    if want_llm:
        try:
            llm = llm_factory()
        except Exception:
            llm = None

    use_llm_summaries = opt.if_add_summary and llm is not None
    use_llm_page_titles = needs_llm_page_title and llm is not None

    if needs_llm_page_title:
        await generate_llm_page_titles_when_no_heading(
            forest,
            model=opt.model,
            llm=llm,
            use_llm=use_llm_page_titles,
            max_title_chars=opt.page_title_llm_max_chars,
        )

    if opt.if_add_summary:
        await generate_node_summaries(
            forest,
            model=opt.model,
            llm=llm,
            use_llm=use_llm_summaries,
            char_threshold=opt.summary_char_threshold,
        )
        doc_summary = await generate_doc_summary(
            forest,
            content,
            line_count,
            model=opt.model,
            llm=llm,
            use_llm=use_llm_summaries,
        )
    else:
        for r in forest:
            _clear_summaries(r)
        doc_summary = ""

    doc_id = opt.doc_id
    if not doc_id:
        seed = doc_id_seed or content[:8000]
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, seed))

    result_trees = strip_forest(forest)
    return {
        "doc_id": doc_id,
        "status": opt.status,
        "retrieval_ready": opt.retrieval_ready,
        "line_count": line_count,
        "summary": doc_summary,
        "result": result_trees,
    }


def sync_build_pageindex_payload_from_lines(
    lines: list[str],
    opt: PageIndexOptions | None = None,
    llm_factory: Callable[[], Any] | None = None,
    *,
    doc_id_seed: str | None = None,
    layout: PageIndexLayout = "h1_forest",
    toc_entries: list[dict[str, Any]] | None = None,
    normalize_levels: bool = True,
) -> dict[str, Any]:
    """``build_pageindex_payload_from_lines`` 的同步包装（内部 ``asyncio.run`` 一次）。

    入参:
        lines: 全文行列表。
        opt: 构建选项。
        llm_factory: LLM 工厂。
        doc_id_seed: 确定性 ``doc_id`` 种子。
        layout: 布局，语义同 ``build_pageindex_payload_from_lines``。
        toc_entries: ``parse_toc_file`` 返回的 TOC 条目列表。
        normalize_levels: 是否执行自动层级规范化。

    返回:
        与 ``build_pageindex_payload_from_lines`` 相同的字典。
    """
    return asyncio.run(
        build_pageindex_payload_from_lines(
            lines, opt, llm_factory, doc_id_seed=doc_id_seed, layout=layout,
            toc_entries=toc_entries, normalize_levels=normalize_levels
        )
    )


def main_argv(argv: list[str] | None = None) -> None:
    """命令行入口：解析参数，生成 JSON 并写入默认或指定的输出路径。

    使用 ``--input-md`` 指定 Markdown（默认可省略，见参数帮助），``--layout`` 选择 ``h1_forest`` 或
    ``page_per_page``（带 ``<!-- page:N -->`` 的 MinerU 类稿件用后者，见 design_md_pageindex §3.1）。

    入参:
        argv: 参数列表；为 None 时使用 ``sys.argv``。

    返回:
        无；成功时打印写出路径。
    """
    parser = argparse.ArgumentParser(
        description=(
            "Build PageIndex JSON from Markdown: --input-md (default combined) + --layout "
            "(h1-forest or page-per-page for paged <!-- page:N --> input)."
        )
    )
    parser.add_argument(
        "--input-md",
        dest="input_md_path",
        type=str,
        default=None,
        help="输入 Markdown 路径；未指定时用仓库内 docs/.../combined_document.md",
    )
    parser.add_argument("--out", dest="out_path", type=str, default=None)
    parser.add_argument("--doc-id", type=str, default=None)
    parser.add_argument("--if-add-summary", type=str, default="yes", choices=("yes", "no"))
    parser.add_argument("--summary-threshold", type=int, default=600)
    parser.add_argument(
        "--page-title-max-chars",
        type=int,
        default=50,
        help="page-per-page 下某页无 #/## 时，LLM/启发式页标题最大字符数（默认 50）",
    )
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--retrieval-ready", type=str, default="no", choices=("yes", "no"))
    parser.add_argument(
        "--layout",
        type=str,
        default="h1-forest",
        choices=("h1-forest", "page-per-page"),
        help="h1-forest：按一级标题分段；page-per-page：文档根 + 每逻辑页一节点（含 MinerU 导出稿）",
    )
    parser.add_argument(
        "--toc-file",
        type=str,
        default=None,
        help="TOC JSON 文件路径（嵌套树形或扁平列表格式），用于覆写标题层级",
    )
    parser.add_argument(
        "--no-level-normalize",
        action="store_true",
        default=False,
        help="跳过自动层级规范化（默认会自动消除标题层级跳跃）",
    )
    args = parser.parse_args(argv)

    base = Path(__file__).resolve().parent
    md_path = (
        Path(args.input_md_path)
        if args.input_md_path
        else base / "docs/results_with_vlm/game2025report_v7/combined_document.md"
    )
    layout_kw: PageIndexLayout = "page_per_page" if args.layout == "page-per-page" else "h1_forest"
    out_path = (
        Path(args.out_path)
        if args.out_path
        else base / "docs/results_with_vlm/game2025report_v7/out_generated.json"
    )

    def llm_factory() -> Any:
        """构造本工程使用的 Qwen/DashScope 兼容异步客户端。

        入参:
            无。

        返回:
            具备 ``acompletion(model, prompt) -> str`` 的客户端实例。
        """
        from llm import QwenChatClient

        return QwenChatClient()

    opt = PageIndexOptions(
        doc_id=args.doc_id,
        retrieval_ready=args.retrieval_ready.lower() == "yes",
        if_add_summary=args.if_add_summary.lower() == "yes",
        summary_char_threshold=args.summary_threshold,
        model=args.model,
        page_title_llm_max_chars=args.page_title_max_chars,
    )

    payload = sync_build_pageindex_payload(
        md_path, opt, llm_factory=llm_factory, layout=layout_kw,
        toc_file=args.toc_file, normalize_levels=not args.no_level_normalize
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main_argv()
