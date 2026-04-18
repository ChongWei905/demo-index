# DemoIndex Stage 1-5 End-to-End Report

## Run Scope

- Date: 2026-04-18
- PDF: `/Users/weichong/Documents/new_working_area/file_tree/DemoIndex/docs/game2025report.pdf`
- Query: `2024 全球手游 CPI 和留存趋势`
- Database action: truncated `document_sections` and `section_chunks`, then rebuilt from scratch
- Build artifact root: `/Users/weichong/Documents/new_working_area/file_tree/DemoIndex/artifacts/game2025report_stage15_e2e_20260418/build`
- Retrieval artifact root: `/Users/weichong/Documents/new_working_area/file_tree/DemoIndex/artifacts/game2025report_stage15_e2e_20260418/retrieve`
- Output tree JSON: `/Users/weichong/Documents/new_working_area/file_tree/DemoIndex/artifacts/game2025report_stage15_e2e_20260418/build/game2025report_pageindex_tree.json`
- Output retrieval JSON: `/Users/weichong/Documents/new_working_area/file_tree/DemoIndex/artifacts/game2025report_stage15_e2e_20260418/retrieve/retrieve_evidence.json`

## Corpus And Index Inventory

- `doc_id`: `6638bb01-25a0-5ddb-a56a-8d40c2adb718`
- PDF page count rendered: `41`
- Extracted visual assets: `49`
- Document tree root count: `8`
- Document tree total node count: `19`
- PostgreSQL `document_sections` rows: `19`
- Leaf sections used for chunking: `13`
- PostgreSQL `section_chunks` rows: `93`
- Embedding model for indexing: `text-embedding-v4` (1024 dims)

## Build / Indexing Phase Summary

- Build API calls: `chat=38`, `embedding=10`, `total_success=48`
- Build token usage: `prompt=58575, completion=58201, total=116776 `

### Build Stage Timings

- `load_pageindex_config`: `0.000s `
- `get_page_tokens`: `0.561s `
- `extract_page_artifacts`: `4.506s `
- `extract_outline_entries`: `0.001s `
- `build_seeded_outline`: `0.001s `
- `build_tree_from_seeded_outline`: `151.302s `
- `write_node_id`: `0.000s `
- `add_node_text`: `0.000s `
- `generate_node_summaries`: `165.458s `
- `convert_output_tree`: `0.003s `
- `persist_document_sections`: `0.024s `
- `build_global_chunk_records`: `16.987s `
- `persist_section_chunks`: `0.151s `
- `save_final_output`: `0.000s `

### Build Output Snapshot

- depth `0` | node `0000` | title `2025年游戏应用洞察报告`
- depth `1` | node `0001` | title `目录`
- depth `1` | node `0002` | title `移动游戏勇攀新巅峰`
- depth `2` | node `0003` | title `游戏开始！AI 让移动游戏领域智能化程度更高、交互更强劲`
- depth `2` | node `0004` | title `开启游戏发现新时代`

### Global Index Snapshot

- section `37c07d4a-919b-5dfb-9f10-6de0fc363005` | node `0001` | chunk `0` | page `3` | tokens `46` | title `目录`
- section `37c07d4a-919b-5dfb-9f10-6de0fc363005` | node `0001` | chunk `1` | page `3` | tokens `384` | title `目录`
- section `37c07d4a-919b-5dfb-9f10-6de0fc363005` | node `0001` | chunk `2` | page `3` | tokens `378` | title `目录`
- section `37c07d4a-919b-5dfb-9f10-6de0fc363005` | node `0001` | chunk `3` | page `3` | tokens `351` | title `目录`
- section `37c07d4a-919b-5dfb-9f10-6de0fc363005` | node `0001` | chunk `4` | page `3` | tokens `393` | title `目录`

## Retrieval Phase Summary (Stage 1-5)

- Retrieval mode: `stage3=hybrid`, `stage5=hybrid`
- Retrieval total duration: `77.612s `
- Retrieval API calls: `chat=2`, `embedding=1`, `total_success=3`
- Retrieval token usage: `prompt=7887, completion=3638, total=11525 `

### Retrieval Stage Timings

- `parse_query`: `0.001s `
- `lexical_recall`: `0.031s `
- `dense_recall`: `2.866s `
- `fuse_chunk_hits`: `0.000s `
- `aggregate_candidates`: `0.008s `
- `stage3_localize_sections`: `33.438s ` (mode=hybrid)
- `stage4_expand_contexts`: `0.024s `
- `stage5_package_evidence`: `41.267s ` (relation_mode=hybrid)

## Stage 1: Query Understanding

- Normalized query: `2024 全球手游 CPI 和留存趋势`
- Language: `mixed`
- Intent: `trend`
- Terms: `2024, 全球手游, 全球, 手游, CPI, 留存趋势, 留存`
- Time scope: `{"years": [2024], "quarters": [], "raw_mentions": ["2024"]}`
- LLM enrichment used: `False`

## Stage 2: Global Candidate Recall

- Lexical search terms: `2024, 全球手游, 全球, 手游, cpi, 留存趋势, 留存`
- Lexical candidate count before top-k: `73`
- Lexical hit count after ranking: `10`
- Dense hit count: `10`
- Fused chunk hit count: `12`

### Fused Chunk Hits (Top 12)

1. section `热门应用，安装、会话和留存率` | page `15` | chunk `16` | branches `lexical#1` | rrf `0.01639344`
   excerpt: 中东北非地区 (30.58 分钟) 和欧洲 (27.54 分钟) 稍有提升 ， 而北 美地区 (24.66 分钟) 和美国 (24.76 分钟) 基本与上年持平。2023 - 2024 年各国家/地区游戏应用会话时长 全球 马来西亚日本印度尼西亚印度亚太 新加坡菲律宾 泰国韩国 欧洲越南 德奥瑞 英国和爱尔兰法国...
2. section `单次安装成本、展示、点击 + 应用合作伙伴数量` | page `25` | chunk `0` | branches `dense#1` | rrf `0.01639344`
   excerpt: 游戏应用洞察报告242023 年 2023 年 2024 年 2024 年2023 - 2024 年游戏应用 D1 留存率 (全球) 2024 年， 全球游戏应用 D1 留存率从 28% 跌至 27%。 棋牌游戏稳定在 22%， 博彩游戏则从 16% 攀升至 19%。 策略和益智问答游戏留存率分别下降至 17% ...
3. section `热门应用，安装、会话和留存率` | page `15` | chunk `8` | branches `lexical#2` | rrf `0.01612903`
   excerpt: 越南会话量年同比跌幅最大， 达到 18%。2023 - 2024 年各国家游戏应用安装量和会话量同比增长率 -20%25% 15% 10% 5% 0% -10% -15%20%安装 会话 印度尼西亚印度 法国 沙特阿拉伯巴西墨西哥日本菲律宾 英国和爱尔兰土耳其 阿联酋美国 越南-12%游戏应用洞察报告202023...
4. section `热门应用，安装、会话和留存率` | page `15` | chunk `20` | branches `dense#2` | rrf `0.01612903`
   excerpt: 棋牌游戏稳定在 22%， 博彩游戏则从 16% 攀升至 19%。 策略和益智问答游戏留存率分别下降至 17% 和 16%。 混合休闲和超休闲游戏早期交 互强劲， D1 留存率依旧领先， 分别为 28% 和 27%。 但到安装后第 30 天， 这两类游戏的留存率都跌 至 2%， 低于整体游戏领域 5%。2024 年...
5. section `热门应用，安装、会话和留存率` | page `15` | chunk `5` | branches `lexical#3` | rrf `0.01587302`
   excerpt: 角色扮演 等类型的游戏更侧重培养玩家忠诚度， 一般会 通过 IAP 变现。3%2024 年不同游戏子类别应用安装量和会话量 (全球) 2% 1%1% 1% 2% 2%1%1% 1% 3% 3% 2%1% 1% 2% 3% 2%2% 3% 2% 1% 安装 会话老虎机解谜 赛车 策略角色扮演 模拟 体育音乐 益智问...
6. section `每用户平均收入` | page `34` | chunk `6` | branches `dense#3` | rrf `0.01587302`
   excerpt: 此外， 您还应 当制定安装后交互策略， 留住用户 ， 高 效 变 现 ，最 大 程 度 地 提 高 长 期 价 值 。游戏应用洞察报告38美元 01.2 1 0.8 0.4 0.20.62023 - 2024 年各国家/地区游戏应用 ARPMAU IA 2023 年 2023 年 2024 年 2024 年202...
7. section `热门应用，安装、会话和留存率` | page `15` | chunk `18` | branches `lexical#4` | rrf `0.015625`
   excerpt: 北美和美国地区每用户会话量垫底， 分别仅有 1.58 和 1.57。2023 - 2024 年各国家/地区游戏应用 D0 每用户会话量 全部 冒险 动作 桌面 街机 博彩 卡牌 家庭 休闲 混合休闲超休闲三消 放置型 RPG解谜 音乐 赛车 益智问答 角色扮演模拟 体育 策略0 02 2 1.2 1.2 1 1 ...
8. section `热门应用，安装、会话和留存率` | page `15` | chunk `17` | branches `dense#4` | rrf `0.015625`
   excerpt: 相较于 上年 (1.86)， 2024 年超休闲和混合休闲游戏的会话量表现依旧无出其右 (1.82)， 但 D1 起会话量急速下滑。 博彩 应用逆势增长， 从 1.57 提升至 1.65； 街机应用也有小幅改善， 从 1.61 涨到 1.63。2024 年， 亚太市场的交互表现依旧强劲， 但印度和印度尼西亚的每用...
9. section `热门应用，安装、会话和留存率` | page `15` | chunk `7` | branches `lexical#5` | rrf `0.01538462`
   excerpt: 角色扮演游戏面对的是 “冰火两 重天” ， 安装量增长强劲 (32%)， 会话量却下滑了 1 2 % ，可 见 在 用 户 留 存 方 面 遇 到 了 挑 战 。2023 - 2024 年游戏应用子类别安装量和会话量同比增长率 (全球) -40%100% 60% 40% 20% 0% -20%80%安装 会话 桌...
10. section `单次安装成本、展示、点击 + 应用合作伙伴数量` | page `25` | chunk `5` | branches `dense#5` | rrf `0.01538462`
   excerpt: 略2023 年 2023 年 2024 年 2024 年2023 - 2024 年游戏应用 CPC (全球) 2024 年， 游戏应用的单次点击成本 (CPC) 保持稳定， 全球中位数为 0.03 美元。 放置型角色扮演游 戏和角色扮演游戏的 CPC 均有所下降， 前者从 0.43 下降到了 0.24 美元， 后...
11. section `热门应用，安装、会话和留存率` | page `15` | chunk `0` | branches `lexical#6` | rrf `0.01515152`
   excerpt: 游戏应用洞察报告14角色扮演游戏中， 应用内购买产生的 ARPMAU 显著提升 ， 从 5.36 美元上涨到 6.48 美元。应用的合作伙伴中位数从 5.7 个增加到 6.2 个 ， 其中混合休闲游戏的合作伙伴最多 ， 达 12.3 个。 全球游戏应用 D1 留存率稍有下滑， 从 28% 降到 27%， 而博彩类...
12. section `热门应用，安装、会话和留存率` | page `15` | chunk `19` | branches `dense#6` | rrf `0.01515152`
   excerpt: 用 D1 留存率从 28% 跌至 27%。 棋牌游戏稳定在 22%， 博彩游戏则从 16% 攀升至 19%。 策略和益智问答游戏留存率分别下降至 17% 和 16%。 混合休闲和超休闲游戏早期交 互强劲， D1 留存率依旧领先， 分别为 28% 和 27%。 但到安装后第 30 天， 这两类游戏的留存率都跌 至 ...

### Stage 2 Doc / Section Aggregation

- doc `6638bb01-25a0-5ddb-a56a-8d40c2adb718` | doc_score `0.08091796` | matched_chunks `12` | matched_sections `3` | top_sections `a9b96de2-b3af-5e99-96df-586016d2765b, ec2e4cb2-4ecc-56da-bb2a-9a8a375a8f13`
  - section `热门应用，安装、会话和留存率` | score `0.0486515` | matched_chunk_count `9` | supporting_chunk_ids `8d41a42c-939b-566e-a422-263be1ccfdb8`
  - section `单次安装成本、展示、点击 + 应用合作伙伴数量` | score `0.03177806` | matched_chunk_count `2` | supporting_chunk_ids `f50922ee-faaa-5826-a46a-7808ce029e66`

## Stage 3: Tree Localization

- Selected doc: `6638bb01-25a0-5ddb-a56a-8d40c2adb718` | tree_size `19`
- Anchor sections: `a9b96de2-b3af-5e99-96df-586016d2765b, ec2e4cb2-4ecc-56da-bb2a-9a8a375a8f13`
- Candidate pool size: `4` | whole_doc_fallback used: `False`
- Heuristic shortlist section_ids: `a9b96de2-b3af-5e99-96df-586016d2765b, ec2e4cb2-4ecc-56da-bb2a-9a8a375a8f13, 766b1f78-84cb-5dda-871a-e5574a018574`
- Stage 3 LLM rerank reasons:
  - `ec2e4cb2-4ecc-56da-bb2a-9a8a375a8f13`: Directly addresses both 2024 global CPI and D1 retention trends with explicit metric breakdowns by region and genre.
  - `a9b96de2-b3af-5e99-96df-586016d2765b`: Covers 2024 global retention and install/session trends comprehensively, but lacks explicit CPI data.
  - `766b1f78-84cb-5dda-871a-e5574a018574`: Parent section focusing primarily on retention trends; broader and less specific than its child section.

### Localized Doc `6638bb01-25a0-5ddb-a56a-8d40c2adb718`
- mode_used: `hybrid`
- anchor titles: `热门应用，安装、会话和留存率, 单次安装成本、展示、点击 + 应用合作伙伴数量`
1. `热门应用，安装、会话和留存率` | relation `anchor` | localization_score `23.874695` | stage2_score `0.0486515`
   title_path: `掌握颠覆性洞见，驱动极致影响力 > 热门应用，安装、会话和留存率`
   reason_codes: `relation:anchor, stage2_section_hit, title_term_match, title_path_term_match, summary_term_match, time_scope_overlap, stage2_score_boost, llm_rerank_selected`
   supporting chunk: `8d41a42c-939b-566e-a422-263be1ccfdb8` | excerpt: 中东北非地区 (30.58 分钟) 和欧洲 (27.54 分钟) 稍有提升 ， 而北 美地区 (24.66 分钟) 和美国 (24.76 分钟) 基本与上年持平。2023 - 2024 年各国家/地区游戏应用会话时长 全球 马来西亚日...
2. `单次安装成本、展示、点击 + 应用合作伙伴数量` | relation `anchor` | localization_score `23.696896` | stage2_score `0.03177806`
   title_path: `揭秘成本与表现数据 > 单次安装成本、展示、点击 + 应用合作伙伴数量`
   reason_codes: `relation:anchor, stage2_section_hit, summary_term_match, time_scope_overlap, stage2_score_boost, llm_rerank_selected`
   supporting chunk: `f50922ee-faaa-5826-a46a-7808ce029e66` | excerpt: 游戏应用洞察报告242023 年 2023 年 2024 年 2024 年2023 - 2024 年游戏应用 D1 留存率 (全球) 2024 年， 全球游戏应用 D1 留存率从 28% 跌至 27%。 棋牌游戏稳定在 22%， 博彩...
3. `揭秘成本与表现数据` | relation `ancestor` | localization_score `10.27303` | stage2_score `0.0`
   title_path: `揭秘成本与表现数据`
   reason_codes: `relation:ancestor, anchor_support_transfer, summary_term_match, time_scope_overlap, llm_rerank_selected`
   supporting chunk: `f50922ee-faaa-5826-a46a-7808ce029e66` | excerpt: 游戏应用洞察报告242023 年 2023 年 2024 年 2024 年2023 - 2024 年游戏应用 D1 留存率 (全球) 2024 年， 全球游戏应用 D1 留存率从 28% 跌至 27%。 棋牌游戏稳定在 22%， 博彩...

## Stage 4: Context Expansion

- Expanded doc: `6638bb01-25a0-5ddb-a56a-8d40c2adb718` | focus sections `a9b96de2-b3af-5e99-96df-586016d2765b, ec2e4cb2-4ecc-56da-bb2a-9a8a375a8f13` | tree_size `19`
- Expanded context count: `2`

### Expanded Doc `6638bb01-25a0-5ddb-a56a-8d40c2adb718`
- Focus `热门应用，安装、会话和留存率` | section `a9b96de2-b3af-5e99-96df-586016d2765b` | localization_score `23.874695`
  - context sections: ancestor::掌握颠覆性洞见，驱动极致影响力; focus::热门应用，安装、会话和留存率
  - evidence chunks: supporting::8d41a42c-939b-566e-a422-263be1ccfdb8@p15#idx16; neighbor::30bf3e9c-4d3d-5a98-b072-887413998c16@p15#idx15; neighbor::f399a6ef-9d8d-5266-91f8-e58fbc7def12@p15#idx17
  - answer_context_text preview: Title Path: 掌握颠覆性洞见，驱动极致影响力 > 热门应用，安装、会话和留存率 [Ancestor] 掌握颠覆性洞见，驱动极致影响力 该部分文档主要总结了2024年全球移动游戏应用市场的核心数据与趋势，涵盖以下关键点：各游戏品类的变现能力（如角色扮演游戏ARPMAU显著提升）、应用合作伙伴数量变化（混合休闲游戏合作渠道最多）、用户留存与互动表现（全球D1留存率微降而博彩类上升，竞速与混合休闲游戏点击率领先，动作游戏平均会话时长最长）、市场安装量增长及区域分布（...
- Focus `单次安装成本、展示、点击 + 应用合作伙伴数量` | section `ec2e4cb2-4ecc-56da-bb2a-9a8a375a8f13` | localization_score `23.696896`
  - context sections: ancestor::揭秘成本与表现数据; focus::单次安装成本、展示、点击 + 应用合作伙伴数量
  - evidence chunks: supporting::f50922ee-faaa-5826-a46a-7808ce029e66@p25#idx0; neighbor::58368666-10a1-5884-aa17-2743b8b60e7b@p25#idx1
  - answer_context_text preview: Title Path: 揭秘成本与表现数据 > 单次安装成本、展示、点击 + 应用合作伙伴数量 [Ancestor] 揭秘成本与表现数据 该部分文档摘自2023-2024年游戏应用洞察报告，主要聚焦于全球及各地区游戏应用的D1（首日）留存率表现与变化趋势。核心要点包括：2024年全球游戏D1留存率整体小幅下滑至27%；分品类来看，混合休闲与超休闲游戏D1留存率领先但30日留存骤降至2%，博彩游戏留存率逆势上升，策略与益智问答游戏留存下降，棋牌游戏保持稳定；分地区来看，亚...

## Stage 5: Evidence Packaging

- Relation mode: `hybrid`
- Labeled evidence_ids: `11b75193-8c2e-5b01-971e-5ff420bfe0f4, 3a9e15a7-c559-52db-97ca-871a96272c6b`
- Final evidence item count: `2`

1. `热门应用，安装、会话和留存率` | evidence_id `3a9e15a7-c559-52db-97ca-871a96272c6b` | score `30.874695` | relation `supports`
   title_path: `掌握颠覆性洞见，驱动极致影响力 > 热门应用，安装、会话和留存率`
   pages: `[15]`
   supporting_chunk_ids: `8d41a42c-939b-566e-a422-263be1ccfdb8 `
   context_section_ids: `95365c54-729c-5144-9acb-3f39e081004a, a9b96de2-b3af-5e99-96df-586016d2765b `
   relationship_reason: 详细提供2024年全球手游D1留存率及会话趋势，但未明确提及CPI数据
   answer_context preview: Title Path: 掌握颠覆性洞见，驱动极致影响力 > 热门应用，安装、会话和留存率 [Ancestor] 掌握颠覆性洞见，驱动极致影响力 该部分文档主要总结了2024年全球移动游戏应用市场的核心数据与趋势，涵盖以下关键点：各游戏品类的变现能力（如角色扮演游戏ARPMAU显著提升）、应用合作伙伴数量变化（混合休闲游戏合作渠道最多）、用户留存与互动表现（全球D1留存率微降而博彩类上升，竞速与混合休闲游戏点击率领先，动作游戏平均会话时长最长）、市场安装量增长及区域分布（...
2. `单次安装成本、展示、点击 + 应用合作伙伴数量` | evidence_id `11b75193-8c2e-5b01-971e-5ff420bfe0f4` | score `28.196896` | relation `supports`
   title_path: `揭秘成本与表现数据 > 单次安装成本、展示、点击 + 应用合作伙伴数量`
   pages: `[25]`
   supporting_chunk_ids: `f50922ee-faaa-5826-a46a-7808ce029e66 `
   context_section_ids: `766b1f78-84cb-5dda-871a-e5574a018574, ec2e4cb2-4ecc-56da-bb2a-9a8a375a8f13 `
   relationship_reason: 直接涵盖2024年全球手游CPI趋势与D1留存率数据，完全匹配查询
   answer_context preview: Title Path: 揭秘成本与表现数据 > 单次安装成本、展示、点击 + 应用合作伙伴数量 [Ancestor] 揭秘成本与表现数据 该部分文档摘自2023-2024年游戏应用洞察报告，主要聚焦于全球及各地区游戏应用的D1（首日）留存率表现与变化趋势。核心要点包括：2024年全球游戏D1留存率整体小幅下滑至27%；分品类来看，混合休闲与超休闲游戏D1留存率领先但30日留存骤降至2%，博彩游戏留存率逆势上升，策略与益智问答游戏留存下降，棋牌游戏保持稳定；分地区来看，亚...

## API Call Appendix

### Build Phase API Calls

- #1 | kind `chat` | model `qwen3.6-plus` | duration `24951ms` | tokens `prompt=466, completion=1223, total=1689`
- #2 | kind `chat` | model `qwen3.6-plus` | duration `25507ms` | tokens `prompt=244, completion=1245, total=1489`
- #3 | kind `chat` | model `qwen3.6-plus` | duration `27659ms` | tokens `prompt=906, completion=1375, total=2281`
- #4 | kind `chat` | model `qwen3.6-plus` | duration `31066ms` | tokens `prompt=623, completion=1564, total=2187`
- #5 | kind `chat` | model `qwen3.6-plus` | duration `18809ms` | tokens `prompt=734, completion=987, total=1721`
- #6 | kind `chat` | model `qwen3.6-plus` | duration `20068ms` | tokens `prompt=732, completion=1046, total=1778`
- #7 | kind `chat` | model `qwen3.6-plus` | duration `26991ms` | tokens `prompt=697, completion=1432, total=2129`
- #8 | kind `chat` | model `qwen3.6-plus` | duration `37740ms` | tokens `prompt=734, completion=2033, total=2767`
- #9 | kind `chat` | model `qwen3.6-plus` | duration `26992ms` | tokens `prompt=591, completion=1428, total=2019`
- #10 | kind `chat` | model `qwen3.6-plus` | duration `23084ms` | tokens `prompt=548, completion=1213, total=1761`
- #11 | kind `chat` | model `qwen3.6-plus` | duration `47911ms` | tokens `prompt=864, completion=2510, total=3374`
- #12 | kind `chat` | model `qwen3.6-plus` | duration `28519ms` | tokens `prompt=662, completion=1525, total=2187`
- #13 | kind `chat` | model `qwen3.6-plus` | duration `24842ms` | tokens `prompt=562, completion=1286, total=1848`
- #14 | kind `chat` | model `qwen3.6-plus` | duration `26030ms` | tokens `prompt=562, completion=1372, total=1934`
- #15 | kind `chat` | model `qwen3.6-plus` | duration `26766ms` | tokens `prompt=715, completion=1432, total=2147`
- #16 | kind `chat` | model `qwen3.6-plus` | duration `31317ms` | tokens `prompt=708, completion=1657, total=2365`
- #17 | kind `chat` | model `qwen3.6-plus` | duration `27140ms` | tokens `prompt=437, completion=1454, total=1891`
- #18 | kind `chat` | model `qwen3.6-plus` | duration `26112ms` | tokens `prompt=439, completion=1391, total=1830`
- #19 | kind `chat` | model `qwen3.6-plus` | duration `30524ms` | tokens `prompt=649, completion=1639, total=2288`
- #20 | kind `chat` | model `qwen3.6-plus` | duration `25378ms` | tokens `prompt=995, completion=1185, total=2180`
- #21 | kind `chat` | model `qwen3.6-plus` | duration `27984ms` | tokens `prompt=330, completion=1365, total=1695`
- #22 | kind `chat` | model `qwen3.6-plus` | duration `33820ms` | tokens `prompt=1381, completion=1673, total=3054`
- #23 | kind `chat` | model `qwen3.6-plus` | duration `38445ms` | tokens `prompt=1304, completion=1926, total=3230`
- #24 | kind `chat` | model `qwen3.6-plus` | duration `16431ms` | tokens `prompt=575, completion=848, total=1423`
- #25 | kind `chat` | model `qwen3.6-plus` | duration `25830ms` | tokens `prompt=575, completion=1376, total=1951`
- #26 | kind `chat` | model `qwen3.6-plus` | duration `18364ms` | tokens `prompt=1179, completion=962, total=2141`
- #27 | kind `chat` | model `qwen3.6-plus` | duration `32553ms` | tokens `prompt=1050, completion=1742, total=2792`
- #28 | kind `chat` | model `qwen3.6-plus` | duration `25280ms` | tokens `prompt=1066, completion=1350, total=2416`
- #29 | kind `chat` | model `qwen3.6-plus` | duration `32321ms` | tokens `prompt=761, completion=1671, total=2432`
- #30 | kind `chat` | model `qwen3.6-plus` | duration `34131ms` | tokens `prompt=838, completion=1836, total=2674`
- #31 | kind `chat` | model `qwen3.6-plus` | duration `29235ms` | tokens `prompt=402, completion=1534, total=1936`
- #32 | kind `chat` | model `qwen3.6-plus` | duration `32662ms` | tokens `prompt=846, completion=1750, total=2596`
- #33 | kind `chat` | model `qwen3.6-plus` | duration `22803ms` | tokens `prompt=552, completion=1145, total=1697`
- #34 | kind `chat` | model `qwen3.6-plus` | duration `18466ms` | tokens `prompt=282, completion=973, total=1255`
- #35 | kind `chat` | model `qwen3.6-plus` | duration `52532ms` | tokens `prompt=4616, completion=2801, total=7417`
- #36 | kind `chat` | model `qwen3.6-plus` | duration `20750ms` | tokens `prompt=1046, completion=1051, total=2097`
- #37 | kind `chat` | model `qwen3.6-plus` | duration `44365ms` | tokens `prompt=3654, completion=2386, total=6040`
- #38 | kind `chat` | model `qwen3.6-plus` | duration `51706ms` | tokens `prompt=1768, completion=2815, total=4583`
- #39 | kind `embedding` | model `text-embedding-v4` | duration `3975ms` | tokens `prompt=2229, completion=0, total=2229`
- #40 | kind `embedding` | model `text-embedding-v4` | duration `1561ms` | tokens `prompt=2153, completion=0, total=2153`
- #41 | kind `embedding` | model `text-embedding-v4` | duration `1706ms` | tokens `prompt=2479, completion=0, total=2479`
- #42 | kind `embedding` | model `text-embedding-v4` | duration `1559ms` | tokens `prompt=2352, completion=0, total=2352`
- #43 | kind `embedding` | model `text-embedding-v4` | duration `1345ms` | tokens `prompt=2690, completion=0, total=2690`
- #44 | kind `embedding` | model `text-embedding-v4` | duration `1341ms` | tokens `prompt=2804, completion=0, total=2804`
- #45 | kind `embedding` | model `text-embedding-v4` | duration `1391ms` | tokens `prompt=3106, completion=0, total=3106`
- #46 | kind `embedding` | model `text-embedding-v4` | duration `1928ms` | tokens `prompt=2535, completion=0, total=2535`
- #47 | kind `embedding` | model `text-embedding-v4` | duration `1316ms` | tokens `prompt=2562, completion=0, total=2562`
- #48 | kind `embedding` | model `text-embedding-v4` | duration `781ms` | tokens `prompt=572, completion=0, total=572`

### Retrieval Phase API Calls

- #1 | kind `embedding` | model `text-embedding-v4` | duration `2821ms` | tokens `prompt=13, completion=0, total=13`
- #2 | kind `chat` | model `qwen3.6-plus` | duration `33395ms` | tokens `prompt=5194, completion=1615, total=6809`
- #3 | kind `chat` | model `qwen3.6-plus` | duration `41238ms` | tokens `prompt=2680, completion=2023, total=4703`

## Key Takeaways

- This end-to-end run rebuilt the corpus from an empty database and produced one document tree plus one global chunk index from scratch.
- The indexing cost is dominated by Stage `build_tree_from_seeded_outline` and `generate_node_summaries`; retrieval cost is dominated by Stage 3 rerank and Stage 5 relation labeling.
- Stage 2 successfully combined dense and lexical retrieval: lexical contributed 10 top hits out of 73 lexical candidates, while dense supplied the CPI-oriented chunks that lexical alone would have missed.
- Stage 3 narrowed the tree to 3 localized sections from a 19-node tree, using 2 anchor sections and a 4-node local candidate pool.
- Stage 4 expanded only 2 focus sections into bounded answer contexts, and Stage 5 packaged them into 2 final evidence items with page references and supporting chunk ids.
