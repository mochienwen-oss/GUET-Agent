# tests/test_citations.py —— 溯源处理的单元测试（纯函数，不需要 LLM/DB/Qdrant）
#
# 这是 L1 唯一能本地验证的一层：端到端要等服务器（本地无 vLLM 生成不了答案），
# 而 318 题是**检索**评测集、不含模型生成答案，拿它验证这里等于空转。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.core import citations as C  # noqa: E402

CTX = [
    {"source": "桂林电子科技大学本科生专业分流及转专业管理办法(修订)",
     "heading_path": ["第三章 转专业", "第十二条"], "score": 0.72},
    {"source": "桂林电子科技大学本科生专业分流及转专业管理办法(修订)",
     "heading_path": ["第三章 转专业", "第十三条"], "score": 0.70},
    {"source": "桂林电子科技大学普通高等教育本科学生学籍管理规定(2025年修订)",
     "heading_path": ["第五章 考核与成绩"], "score": 0.66},
]


# ---------------------------------------------------------------- 注入块
def test_block_dedupes_and_keeps_order():
    b = C.build_source_block(CTX)
    assert b.startswith(C.SOURCE_PREFIX)
    assert b.count("本科生专业分流及转专业管理办法(修订)") == 1     # 同 source 去重
    assert b.index("专业分流") < b.index("学籍管理规定")              # 保持检索名次
    assert "📌" not in b and "📍" not in b and "🔖" not in b          # 全链路去 emoji


def test_block_keeps_all_distinct_used_paths_not_just_first_two():
    contexts = [{'source': '某规定', 'heading_path': [p]}
                for p in ('第五条', '第十一条', '第十二条', '第五条')]
    block = C.build_source_block(contexts)
    assert block == '资料来源：《某规定》 第五条；第十一条；第十二条'


def test_block_single_source_not_numbered():
    b = C.build_source_block(CTX[:1])
    assert b.startswith(C.SOURCE_PREFIX + "《")                       # 「资料来源：《…》」
    assert "\n1." not in b


def test_block_heading_path_empty_ok():
    b = C.build_source_block([{"source": "X办法", "heading_path": []}])
    assert b == f"{C.SOURCE_PREFIX}《X办法》"


def test_block_empty_contexts_no_injection():
    # 门控判空时本来就没有来源可标 → 不注入
    assert C.build_source_block([]) == ""
    assert C.build_source_block([{"source": "", "heading_path": []}]) == ""


# ---------------------------------------------------------------- 剥离（`_RE_SRC_LINE` 认得哪些写法）
# 2026-10-02：独立的 `strip_source_line()` 已删除（生产从来只走 finalize_answer 的全文行扫描，
# 它是死代码）。这些用例改在 **finalize_answer 层面**验证，测的是同一条正则、同一条路径。
def test_source_line_variants_are_all_stripped():
    """三种 emoji / 加粗 / 全角冒号 / `>` `###` 前导 —— 全部要认得。

    前缀 emoji 是**刻意三等分**的（训练集 1000 条实测：📌234 / 📍233 / 🔖233，三种都会出现）。
    """
    for line in ("📌 资料来源：【X】", "📍 资料来源：【X】", "🔖 资料来源：【X】",
                 "**资料来源**：【X】", "资料来源:【X】", "> 资料来源：【X】", "### 资料来源：【X】"):
        out, ok = C.finalize_answer(f"正文要点。\n{line}", [])
        assert ok, line
        assert "资料来源" not in out, line
        assert "正文要点。" in out, line


def test_finalize_truncates_from_source_line_to_end():
    """★ 这里固化的是**实测行为**，不是理想行为 —— 写清楚是为了让它可见。

    `finalize_answer` 从最后一条来源行**截断到末尾**，所以模型跟在来源行之后的
    免责声明（「每年政策可能微调，以当年通知为准」）会被一并去掉。

    为什么会有这种落差：旧的 `strip_source_line` 曾刻意「只删那一行、保留免责声明」，
    但它**从来不在生产路径上**（生产只走 `finalize_answer`）—— 那份意图从未生效，
    只留在了注释里。要不要改成保留免责声明，得连同答案文本一起重跑评测才谈得上，
    所以此处先如实记录，不擅自改行为。
    """
    out, ok = C.finalize_answer("1. 要点。\n📌 资料来源：【X办法】\n每年政策可能微调，以当年通知为准。", [])
    assert ok
    assert "资料来源" not in out
    assert "以当年通知为准" not in out       # ← 免责声明也被截掉（已知取舍，非本次改动引入）


def test_finalize_keeps_body_before_source_line():
    """截断点**之前**的正文必须一字不少。"""
    body = "第一段要点。\n第二段要点。"
    out, ok = C.finalize_answer(body + "\n📌 资料来源：【X】\n尾部", [])
    assert ok and out == body


def test_finalize_does_not_touch_body_mention():
    """正文中段「依据资料来源：」不算来源行（行首锚定 + `依据` 是词字符，不属前导类）。"""
    out, ok = C.finalize_answer("依据资料来源：学校规定，学生可以申请。\n下一句。", [])
    assert not ok and "可以申请" in out


def test_finalize_truncates_from_last_source_line():
    """多段自写来源（v3 实测形态：条号拼接段 + 模仿【参考资料】块头的第二段）→
    从**最后一条**来源行整段截断，其后的内容一并去掉。

    为什么取最后一条而不是第一条：模型在正文里合法提到「…」（少见但会），
    从第一条截断会误删正文。取最后一条只多留一点模型痕迹，不会伤内容 ——
    宁可留痕，不可误删。"""
    out, ok = C.finalize_answer("正文。\n资料来源：【A】\n第二段来源：\n资料来源：【B】\n尾巴", [])
    assert ok and "【B】" not in out and "尾巴" not in out and "正文。" in out


def test_finalize_no_source_line_returns_unchanged():
    out, ok = C.finalize_answer("一段没有任何来源行的正文。", [])
    assert (out, ok) == ("一段没有任何来源行的正文。", False)
    assert C.finalize_answer("", []) == ("", False)


# ---------------------------------------------------------------- 流式截断（2026-10-02 新契约）
def test_streamable_cut_emits_everything_when_no_source_line():
    """★ 「不是流式输出」的**回归测试**。

    旧实现恒定扣尾 2000 字，而实测答案裸长度 p50 = 223 / 最大 942 —— 扣得比答案全长还多，
    于是流式循环一个字都不发。新版在无来源行时必须返回**全长**（全部立刻发出）。
    """
    body = "要点一：转专业按管理办法办理。\n要点二：需要提交申请表。"
    assert C.streamable_cut(body, 0) == len(body)
    assert C.streamable_cut(body + "后", 0) == len(body) + 1
    assert C.streamable_cut("", 0) == 0


def test_streamable_cut_holds_model_written_source_line():
    """来源行一旦出现，从它开始就不再发出 —— 那一段会被真实来源块整段替换，先发只会闪烁。"""
    body = "答案正文。" * 5
    full = body + "\n📌 资料来源：【泛称办法】\n后面这句也会被替换掉"
    cut = C.streamable_cut(full, 0)
    assert cut == len(body) + 1                  # 保留它前面那个换行
    assert "资料来源" not in full[:cut]


def test_streamable_cut_uses_last_source_line():
    """与 finalize_answer 同口径（取**最后一条**）—— 两处不一致会让来源块闪现。"""
    full = "正文\n资料来源：【A】\n中间\n资料来源：【B】\n尾巴"
    cut = C.streamable_cut(full, 0)
    assert "【B】" not in full[:cut]
    assert "中间" in full[:cut]                  # 第一段来源之前的内容照常发出


def test_streamable_cut_never_rewinds_below_emitted():
    """已发出的前缀绝不回收 —— SSE 收不回来。返回值恒 ≥ emitted。"""
    body = "已经发出去的一段正文。"
    full = body + "\n资料来源：【X】"
    assert C.streamable_cut(full, len(full)) == len(full)
    assert C.streamable_cut(full, len(body) + 5) == len(full)


def test_streamable_cut_holds_when_source_line_starts_exactly_at_emitted():
    """★ 边界（**实测抓出来的真 bug**）：来源行**刚好从已发位置开始**。

    流式下这很常见 —— 换行先到一个 delta、来源行下一个 delta 才到，
    于是 `emitted` 正好等于来源行的起点。这里必须返回 `emitted`（一个字都不发）。
    第一版写成 `i > emitted` 就退化成"无需扣"，把来源行放出去闪一下；
    单元测试没覆盖到这个等号，是集成测试（`test_streaming_loop_...`）当场抓出来的。
    """
    body = "正文一段。"
    full = body + "\n📌 资料来源：【X】"
    at = len(body) + 1                        # 来源行起点的偏移
    assert C.streamable_cut(full, at) == at   # 恰好等于 → 一个字都不发
    assert C.streamable_cut(full, at - 1) == at
    assert C.streamable_cut(full, at + 3) == len(full)   # 已经越过去了 → 收不回来，照发


def test_streaming_loop_holds_source_line_and_matches_persisted():
    """★ 端到端复核 bug① 的契约：把 `chat.py` 的流式循环原样模拟一遍。

    断言三件事，缺一不可：
      ① **正文真的流出去了**（不是攒到最后一次性发）—— 旧实现在这里发出 0 个字；
      ② 模型自写的来源行**没有**流出去（被 streamable_cut 扣住，不闪一下再消失）；
      ③ 流末尾的 `{"replace": ...}` 末事件 == `finalize_answer` 的结果 == 落库内容。
    """
    deltas = ["转专业按本科生专业分流及转专业管理办法办理。", "需要提交申请表。",
              "\n📌 资料来源：【泛称办法】", "\n尾部这些也不该被看到"]
    full, emitted, streamed = "", 0, ""
    for d in deltas:
        full += d
        cut = C.streamable_cut(full, emitted)
        if cut > emitted:
            streamed += full[emitted:cut]
            emitted = cut

    assert "转专业按本科生专业分流及转专业管理办法办理。" in streamed   # ① 边收边发
    assert "泛称办法" not in streamed                                    # ② 来源行被扣住
    assert "尾部这些也不该被看到" not in streamed

    finalized, _ = C.finalize_answer(full, CTX)     # ← chat.py 末尾那条 replace 事件的内容
    assert finalized.startswith(streamed)           # ③ 末事件是流出前缀的超集，不是无关文本
    assert "泛称办法" not in finalized               # 模型自写来源被真实来源块替换
    assert "本科生专业分流及转专业管理办法(修订)" in finalized


# ---------------------------------------------------------------- finalize（整篇）


def test_finalize_short_answer_no_tail_split():
    full = "简短回答。\n📌 资料来源：【X】"
    finalized, ok = C.finalize_answer(full, CTX)
    assert ok and "简短回答。" in finalized and "【X】" not in finalized


def test_finalize_strip_failure_keeps_and_appends():
    """剥离失败（没来源行）→ 正文原样；**确有引用**时仍要追加真实来源。"""
    full = "答：按本科生专业分流及转专业管理办法，可以申请。"
    finalized, ok = C.finalize_answer(full, CTX)
    assert not ok                                       # 没有自写来源行可剥
    assert "可以申请。" in finalized and C.SOURCE_PREFIX in finalized


def test_finalize_no_reference_no_injection():
    """★ 零匹配不注入（宁缺勿错）——正文没引用任何上下文文档时，一份来源都不标。"""
    finalized, _ = C.finalize_answer("答：可以申请。", CTX)
    assert finalized == "答：可以申请。"
    assert C.SOURCE_PREFIX not in finalized


def test_finalize_empty_contexts_no_block():
    finalized, _ = C.finalize_answer("答：可以申请。", [])
    assert finalized == "答：可以申请。"


# ---------------------------------------------------------------- 来源过滤终版（2026-10-01）
def test_doc_match_keys_cover_full_core_fragment():
    """★ 匹配键覆盖 全名 / 核心名 / 特征名 / 专业名 —— 正文常写短名或片段。"""
    assert C._doc_match_keys(
        "桂电教〔2020〕15号--关于印发《桂林电子科技大学考试管理规定》的通知"
    ) == ["桂林电子科技大学考试管理规定", "考试管理规定"]
    assert C._doc_match_keys(
        "《桂林电子科技大学本科生专业分流及转专业管理办法(修订)》(桂电教〔2021〕6号)"
    ) == ["桂林电子科技大学本科生专业分流及转专业管理办法(修订)",
          "本科生专业分流及转专业管理办法", "本科生专业分流及转专业"]
    assert C._doc_match_keys("2025级交通工程专业培养方案(主修)") == [
        "2025级交通工程专业培养方案(主修)", "2025级交通工程专业培养方案",
        "2025级交通工程专业", "交通工程专业"]


def test_doc_match_keys_drops_generic_only_tail():
    # 「管理办法」剥完只剩 2 字，太泛 → 不作为键（否则任何正文都命中）
    assert "办法" not in C._doc_match_keys("X管理办法")
    assert C._doc_match_keys("") == []


def test_referenced_sources_zero_match_returns_empty():
    """★ 零匹配 → 空列表（**不再兜底 top-1**）。"""
    assert C._referenced_sources("我用了该办法，按规定办。", CTX) == []
    assert C._referenced_sources("", CTX) == []
    assert C._referenced_sources("随便一句", []) == []


def test_referenced_sources_matches_by_core_and_keeps_order():
    # 核心名命中第一份（CTX 里前两条是同一文档的两个块）
    h2 = C._referenced_sources("依据本科生专业分流及转专业管理办法办理。", CTX)
    assert len(h2) == 2 and all("专业分流" in c["source"] for c in h2)
    # 只命中第二份（学籍管理规定）
    h3 = C._referenced_sources("按普通高等教育本科学生学籍管理规定执行。", CTX)
    assert [c["source"] for c in h3] == [
        "桂林电子科技大学普通高等教育本科学生学籍管理规定(2025年修订)"]
    # 两份都提到 → 都保留（第二份用核心名，泛称「学籍管理规定」不是匹配键，见 318 报告）
    h4 = C._referenced_sources(
        "转专业按本科生专业分流及转专业管理办法，成绩按普通高等教育本科学生学籍管理规定。", CTX)
    assert len({c["source"] for c in h4}) == 2


# ---------------------------------------------------------------- L2（已接线·观察型）
def test_check_citations_out_of_range():
    assert C.check_citations("依据[1]和[3]的规定", 2) == [
        {"kind": "out_of_range", "n": 3, "n_refs": 2, "span": [6, 9], "text": "[3]"}]
    assert C.check_citations("依据[1][2]", 2) == []


def test_check_citations_variants():
    assert len(C.check_citations("见[1,3]", 2)) == 1        # 逗号
    assert len(C.check_citations("见[1-3]", 3)) == 0        # 区间，全在范围内
    assert len(C.check_citations("见[1-4]", 3)) == 1        # 区间，越界
    assert len(C.check_citations("见【4】", 3)) == 1        # 全角


def test_check_citations_ignores_markdown_link_and_table():
    assert C.check_citations("见[9](https://x)", 3) == []   # markdown 链接
    assert C.check_citations("| 序号 |\n| [9] | 值 |", 3) == []   # 表格行内
    assert C.check_citations("", 3) == []


def test_check_citations_no_false_positive_on_doc_numbers():
    """★ 文号不得误报 —— 语料里文号遍地都是，这条判据错了会满屏假警报。

    实测形态（都是本语料真实写法）：`桂电教〔2025〕27号`（〔〕不是方括号）、
    `（教高【2019）6号）`（括号不配对）、`[2019]` `【2024】`（4 位数字超出 `\\d{1,3}`）。
    """
    for s in ["依据桂电教〔2025〕27号", "（教高【2019）6号）", "见[2019]", "见【2024】",
              "依据《…》(桂电学位〔2025〕19号)办理", "第1-3条", "工作量×0.5"]:
        assert C.check_citations(s, 3) == [], f"文号被误判成 [n] 越界：{s!r}"


def test_violation_summary():
    assert C.violation_summary([]) == ""            # 无越界 → 空串（调用方据此跳过打点）
    s = C.violation_summary(C.check_citations("依据[1]和[3]", 2))
    assert "越界" in s and "n=[3]" in s and "n_refs=2" in s
    # 多处越界要能去重汇总
    s2 = C.violation_summary(C.check_citations("见[7][9][7]", 3))
    assert "n=[7, 9]" in s2


# ---------------------------------------------------------------- 来源名剥壳
def test_short_title_strips_wrappers():
    """★ 语料文件名带公文外壳，注入前必须剥成**简短真名**。

    4 种形态都是 `_md` 里**实测存在**的（90 份里 21 份含《》、另 2 份是「文号--正文」）。
    不剥的后果有两处：用户看到的来源嵌套书名号且带一长串文号；块头同样是嵌套书名号。
    更要紧的是**训练集里答案写的都是简短真名**（原流水线 patch_sources.py 就剥了），
    不剥 = 训练与运行时不一致。
    """
    cases = {
        "关于印发《桂林电子科技大学博士后管理工作实施办法》的通知(桂电人〔2022〕2号)":
            "桂林电子科技大学博士后管理工作实施办法",
        "《桂林电子科技大学“大学英语”课程替换及成绩认定办法(试行)》(校教〔2025〕5号)":
            "桂林电子科技大学“大学英语”课程替换及成绩认定办法(试行)",
        "桂电教〔2015〕67号--关于印发《桂林电子科技大学教室使用管理办法》的通知":
            "桂林电子科技大学教室使用管理办法",
        "桂电教〔2019〕10号--桂林电子科技大学普通全日制本(专)科学生转学工作实施细则(修订)":
            "桂林电子科技大学普通全日制本(专)科学生转学工作实施细则(修订)",
    }
    for raw, want in cases.items():
        assert C.short_title(raw) == want, (raw, C.short_title(raw))


def test_short_title_leaves_plain_names_alone():
    # 培养方案 / 章程这类文件名**本来就是真名**，不许被改
    for s in ["2025级交通工程专业培养方案(主修)",
              "桂林电子科技大学章程(2023年核准稿)",
              "桂林电子科技大学普通高等教育本科学生学籍管理规定(2025年修订)"]:
        assert C.short_title(s) == s


def test_short_title_never_returns_a_stub():
    """剥完太短就当没剥成 —— 宁可留外壳，不可剥成残名。"""
    assert C.short_title("《短》的通知(桂电〔2020〕1号)") == "《短》的通知(桂电〔2020〕1号)"
    assert C.short_title("") == ""


def test_short_title_strips_the_markdown_extension():
    """★ `.md` 是**内部文件形态**，不是文件真名的一部分。

    来源不一致的实测成因：规章类文件名不带后缀，而培养方案类工具的
    `Source.title` 直接取 plan_db 的 `file` 字段（带 `.md`）—— 同一份回答里
    来源行于是两种形态并存。剥在**唯一的标题清洗入口**，两种形态都覆盖。
    """
    assert C.short_title("2025级交通工程专业培养方案(主修).md") == \
        "2025级交通工程专业培养方案(主修)"
    # 大小写不敏感
    assert C.short_title("2025级交通工程专业培养方案(主修).MD") == \
        "2025级交通工程专业培养方案(主修)"
    assert C.short_title("培养方案.Markdown") == "培养方案"
    # ★ 《标题.md》形态：先从《》里取出内容，再剥扩展名。
    assert C.short_title("《桂林电子科技大学考试管理规定.md》") == \
        "桂林电子科技大学考试管理规定"
    # 没有扩展名的一律不动（既有三条规则的行为不变）。
    assert C.short_title("2025级交通工程专业培养方案(主修)") == \
        "2025级交通工程专业培养方案(主修)"


def test_stripping_the_extension_does_not_touch_the_internal_title():
    """★ 只在**用户可见标题**上剥 —— 内部 `Source.title` 与 evidence 身份不变。

    evidence ID 由 `identity(source)` 算，它用的是**原始** title；如果在这里
    改了数据层，binding / catalog / 索引身份都会跟着漂 —— 那是另一件事，
    且会让冻结包与候选对不上。
    """
    from agent.core.contracts import Source
    from agent.core.evidence import EvidenceLedger
    raw = "2025级交通工程专业培养方案(主修).md"
    src = Source(title=raw, heading_path=[], text="正文。")
    ledger = EvidenceLedger()
    added = ledger.add("query_course", {"course": "工程力学"}, src)
    assert ledger.get(added.evidence_id).source.title == raw          # 原始 title 原样
    assert C.short_title(raw) == "2025级交通工程专业培养方案(主修)"    # 只是显示层


def test_a_multi_heading_block_shows_only_the_document_title():
    """★ tq-012 的实测缺陷：块横跨多条，块起点的条号**指错了**。

    证据块是「第三条…第六条」那一段，来源行却写「第三条 教室使用的原则」，
    而回答用的是「第四条 借用教室需办理的手续」—— 一条看着很精确的**错误**定位。
    块里出现多个 Markdown 条文标题时，只显示文档标题。
    """
    snippet = ("### 第三条  教室使用的原则\n教室使用应……\n"
               "### 第四条  借用教室需办理的手续\n借用教室应提前……")
    assert C.display_heading_path(["第三条  教室使用的原则"], snippet) == []
    block = C.build_source_block([{
        "source": "桂电教〔2015〕67号--关于印发《桂林电子科技大学教室使用管理办法》的通知",
        "heading_path": C.display_heading_path(["第三条  教室使用的原则"], snippet)}])
    assert block == "资料来源：《桂林电子科技大学教室使用管理办法》"
    assert "第三条" not in block


def test_a_single_heading_block_keeps_its_locator():
    """只有一个标题（或没有）时块起点就是正确归属 —— 原样保留。"""
    one = "### 第四条  借用教室需办理的手续\n借用教室应提前一个工作日办理。"
    assert C.display_heading_path(["第四条  借用教室需办理的手续"], one) == \
        ["第四条  借用教室需办理的手续"]
    assert C.display_heading_path(["第三章 总则"], "") == ["第三章 总则"]
    assert C.display_heading_path(None, "") is None
    # 标题行必须**行首**才算：正文里提到「###」不算。
    assert C.display_heading_path(["第三章"], "正文里提到 ### 不算标题") == ["第三章"]


def test_block_uses_short_title():
    b = C.build_source_block([{"source": "桂电教〔2020〕15号--关于印发《桂林电子科技大学考试管理规定》的通知",
                               "heading_path": ["第八条"]}])
    assert "桂电教〔2020〕15号" not in b
    assert "《桂林电子科技大学考试管理规定》 第八条" in b


def test_clean_path_drops_redundant_and_paragraphs():
    """★ 路径只留**能当定位用的那几段**；两类实测脏值都要挡掉。

    ① 路径元素就是 md 首行的**通知标题** → 与来源名重复（实测 76 处）；
    ② 路径元素是**整段正文**（标题识别误判，实测 94 处、最长 584 字）。
    """
    src = "桂电教〔2023〕12号--关于印发《桂林电子科技大学本科课堂教学规范》的通知"
    assert C.clean_path(["关于印发《桂林电子科技大学本科课堂教学规范》的通知(桂电教〔2023〕12号)"], src) == []
    assert C.clean_path(["第三章 课程考核", "第八条"], src) == ["第三章 课程考核", "第八条"]
    # 整段当标题 → 丢掉
    assert C.clean_path(["十、本条件中,成果转化收益的核算:符合《X》条件的到位技术交易金额或技术作价金额的50%。"], src) == []
    # 去重、保序
    assert C.clean_path(["第三章", "第三章", "第八条"], src) == ["第三章", "第八条"]
    # 空 path / None
    assert C.clean_path(None, src) == [] and C.clean_path([], src) == []


def test_clean_path_tolerates_string():
    """★ 字符串形态必须容忍 —— 否则 `for e in "第八章…"` 会**逐字符**拆，
    块头变成「第 / 八 / 章 / …」（实测踩过：块头被撑到 5960 字）。"""
    assert C.clean_path("第三章 课程考核 / 第八条", "") == ["第三章 课程考核", "第八条"]


# ---------------------------------------------------------------- Layer 2：真实分布回放
# 用 训练集/train.jsonl（1000 条真实训练目标 = 模型的行为分布）做回归：
# 任何改剥离正则/窗口的改动，只要让「漏剥」或「误伤」不再为 0，这里立刻失败。
_TRAIN = Path(__file__).resolve().parents[2] / "训练集" / "train.jsonl"


def test_replay_train_jsonl_no_miss_no_false_strip():
    """用 `训练集/train.jsonl`（1000 条真实训练目标 = 模型的行为分布）做回归：
    任何改动，只要让**漏剥**或**误伤**不再为 0，这里立刻失败。

    ★ 2026-10-02：判据从「尾部窗口内剥」升级为「`finalize_answer` 全文剥」——
      窗口已经不存在了（那正是「不是流式输出」的病灶），所以**漏剥从此不该发生**；
      这条测试因此同时守两件事：正则**认得出**来源行、且**不误伤**正文。
    """
    if not _TRAIN.is_file():
        import pytest
        pytest.skip("训练集/train.jsonl 不在，跳过回放")
    import json
    ans = [m["content"] for l in open(_TRAIN, encoding="utf-8")
           for r in [json.loads(l)] for m in r["messages"] if m["role"] == "assistant"]
    assert len(ans) >= 900
    stripped = false_strip = with_src = 0
    for a in ans:
        # ground truth：行级正则扫全文，事实上有没有来源行
        line_hit = any(C._RE_SRC_LINE.match(ln) for ln in a.split("\n"))
        out, ok = C.finalize_answer(a, [])      # 空 contexts → 只看剥离，不看注入
        with_src += line_hit
        stripped += ok
        false_strip += ok and not line_hit
        if ok:
            assert not any(C._RE_SRC_LINE.match(ln) for ln in out.split("\n")), \
                f"剥完仍残留来源行：{out[-60:]!r}"
    assert with_src >= 600, f"训练集里带来源行的比例异常：{with_src}"
    assert false_strip == 0, f"有 {false_strip} 条无来源行却被剥了（正则误伤）"
    assert stripped == with_src, f"剥成功 {stripped} ≠ 含来源行 {with_src}（有漏剥）"

# ------------------------------------------------ build_messages 的两个契约（2026-09-28）
# 这两条都是**实测踩出来的**，不是凑覆盖率：一条是红线校验的 N 会高估、一条是悬空指令。

def test_build_messages_returns_actual_ref_count():
    """★ `n_refs` 必须是**实际注入的条数**，不是 `len(contexts)`。

    为什么 —— `MAX_CONTEXT_CHARS` 的预算循环会提前 `break`，实际条数可能**少于** ctx 条数。
    调用方若拿 `len(contexts)` 当 N 去校验 `[n]` 越界，N 被高估 → **漏判**真越界的引用。
    实测：5 条超长 ctx 只注入 1 条，而旧代码传的是 5 —— `[2]`~`[5]` 全被判合法。
    """
    from llm import llm_client
    ctx = [{"source": "某办法", "text": "丙" * 4000, "heading_path": []} for _ in range(5)]
    msgs, n = llm_client.build_messages([], "问题", ctx)
    assert n == 1 and n < len(ctx), n                    # ← 锁住「会截断」这个事实
    small = [{"source": "某办法", "text": "短正文", "heading_path": []} for _ in range(3)]
    assert llm_client.build_messages([], "问题", small)[1] == 3
    assert llm_client.build_messages([], "问题", [])[1] == 0


def test_block_suffix_does_not_ask_for_citation_markers():
    """★ 运行时**不再要求**模型写 `[n]`（2026-09-28 决策 B）。

    理由：688 条带块样本的 system 全带「用 [1][2] 小标号标注」那句，而答案里用 `[n]` 的是
    **0/688** —— 训练数据自己教的是「这句可以忽略」。指令悬空，而**零示范的引用最容易编造**。
    前端也不解析 `[n]`（来源由 retrieved_docs 渲染），所以删掉不损失功能。
    改回去要连同 688 条样本的 system 尾巴一起改（`_work/sync_system.py` 第二项作业）。
    """
    from llm import llm_client
    ctx = [{"source": "某办法(试行)", "text": "正文" * 100, "heading_path": ["第一章总则"]}]
    sysmsg = llm_client.build_messages([], "问题", ctx)[0][0]["content"]
    assert "【参考资料】" in sysmsg
    assert "小标号" not in sysmsg and "[1][2]" not in sysmsg
    # 兜底那句（红线相关）必须留着 —— 删的是引用要求，不是「不要编造」
    assert "不要编造" in sysmsg and "不能凭记忆直接作答" in sysmsg


# ---------------------------------------------------------------- Task 5.2 提取的公开剥离函数
# `strip_model_sources` 是 `finalize_answer` 剥离逻辑的**公开单一实现**；
# 下面两条钉的是「它确实与 finalize 同口径」，而不是「它看起来对」。


def test_strip_model_sources_agrees_with_finalize_byte_for_byte():
    """★ 同一个实现的两张脸 —— 对任意输入，两者的正文必须逐字相同。"""
    samples = [
        "正文要点。",
        "正文要点。\n📌 资料来源：【X办法】",
        "第一段。\n第二段。\n资料来源：《甲》\n资料来源：《乙》",
        "依据资料来源：学校规定，学生可以申请。\n下一句。",
        "正文。\n> 资料来源：【X】\n每年政策可能微调，以当年通知为准。",
        "",
        "   ",
    ]
    for text in samples:
        final_body, _ = C.finalize_answer(text, [])
        assert C.strip_model_sources(text) == final_body, repr(text)


def test_strip_model_sources_truncates_from_the_last_source_line():
    """★ 从**最后一条**来源行截断（既有口径）：**更早**那条来源行会留下。

    这是 `finalize_answer` 刻意的取舍 —— 「宁可留痕，不可误删正文」
    （模型偶尔在正文里合法提到来源名，从第一条截断会伤内容）。

    ★ 但「留痕」对**训练候选**不可接受：那等于把一条编造来源写进训练目标。
      所以 `answer_candidates.draft_candidate` 剥完会**再查一遍**，
      仍剩来源行的一律进 redteam（`unstrippable_sources`）——
      本文件只固化 `strip_model_sources` 自身的行为。
    """
    body = "第一段要点。\n第二段要点。"
    assert C.strip_model_sources(body + "\n📌 资料来源：【甲】\n尾部") == body
    assert (C.strip_model_sources("正文。\n资料来源：《甲》\n资料来源：《乙》")
            == "正文。\n资料来源：《甲》")


def test_strip_model_sources_leaves_a_body_mention_alone():
    """正文中段的「依据资料来源：」不是来源行（行首锚定 + `依据` 是词字符）。"""
    text = "依据资料来源：学校规定，学生可以申请。\n下一句。"
    assert C.strip_model_sources(text) == text.rstrip()


def test_strip_model_sources_on_empty_input_returns_empty():
    assert C.strip_model_sources("") == ""
    assert C.strip_model_sources(None) == ""
