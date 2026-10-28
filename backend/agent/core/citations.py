# citations.py —— 生成后的**溯源处理**（纯函数，不依赖 LLM / DB / 网络）
#
# 为什么是「后端注入真实来源」而不是「校验模型写对」——
# 实测（`训练集/train.jsonl` 1000 条真实训练目标 = 模型的行为分布）：
#   · 输出里 `[n]` 出现率 **0/1000** —— LoRA 从未被训练输出编号引用，
#     「用 [n] 标注」只来自运行时追加的【参考资料】指令块，不在训练分布里；
#   · 「资料来源」行前缀 emoji **📌234 / 📍233 / 🔖233**（刻意三等分，三种都会出现）；
#   · 来源名能对上语料真实标题的只有 **23/700** —— 模型写的是**泛称**
#     （「本科生转专业管理办法」），而语料真实标题是
#     《桂林电子科技大学本科生专业分流及转专业管理办法(修订)》。严格校验会误报 97%。
# 结论：模型的命名习惯不支持「校验它写对」。而**检索侧本来就知道 ground truth**
# （这次给模型的是哪几份文档），所以溯源由后端注入更可靠 —— 确定性、不可能幻觉、零 LLM 依赖。
#
# 本模块分两部分：
#   ✅ **已接入运行时**：build_source_block / finalize_answer（由 routers/chat.py 调用）
#   ✅ **已接入（观察型）**：check_citations。2026-09-28 接线，只打日志、不改答案 ——
#      它当初「就绪但未接线」等的是「模型到底吐不吐 [n]」这个实测，接线就是为了装上观测点。
#      为什么不改答案：见 violation_summary（会打破「流出=落库」不变量）。

import re

# ★ 2026-10-02 删掉了 `TAIL_HOLD = 2000`（**这是「不是流式输出」的根因**）。
#
# 它原本的作用：恒定扣住末尾 2000 字不发，好在流结束后把模型自写的来源行替换掉
# —— 因为 SSE 已发出的字收不回来，只能靠「不敢发」来保住「流出=落库」。
#
# 为什么必须删（实测，不是推理）：拿 444 条生产同源输出（`训练集/_work/eval/eval_v2f.jsonl`）
# 量答案裸长度 ——
#     答案裸长度      p50 = 223 字 · p90 = 475 · p99 = 780 · 最大 942
#     被改写的尾巴    p50 =  18 字 · p90 =  63 · p99 = 506 · 最大 731
# 扣住的字数（2000）**恒大于答案全长**，于是 `chat.py` 里
# `len(full) - emitted > TAIL_HOLD` **永远为假** → 流式循环一个字都不发，
# 整段答案在流结束那一刻一次性吐出。100% 触发，不是偶发。
# 顺带也说明 2000 是所需值的 3 倍有余（真正要扣的最长只有 731）。
#
# 现在的分工（两条各司其职，比原来那条「永远不敢发的尾巴」稳得多）：
#   · `streamable_cut()` —— 只扣住**尾部那段会被 finalize 改写的来源块**，其余立刻发出；
#   · `chat.py` 流末尾发一条**权威全文**事件 `{"replace": ...}`，前端以它为准。
# 「用户最终看到的 = 落库的」由**末事件**保证，不再依赖任何"发出去了就不能改"的约束。

# 注入块的前缀。**纯文字、不带 emoji**（全链路去 emoji 的约定）；
# 前端后续识别该前缀整块接管渲染（加 SVG 图标）。
SOURCE_PREFIX = "资料来源："

# ---------------------------------------------------------------- 来源名的「简短真名」
#
# 为什么要剥 —— 语料文件名带着**公文外壳**（`桂电教〔2020〕15号--关于印发《X》的通知`），
# 而 Qdrant payload 的 `source` 就是文件名。直接注入会变成：
#   · 用户看到的：`资料来源：《桂电教〔2020〕15号--关于印发《桂林电子科技大学考试管理规定》的通知》`
#     —— 嵌套书名号 + 一长串文号，很难看；
#   · 块头变成 `[1]《桂电教〔2020〕15号--关于印发《X》的通知》`，同样嵌套。
# 而**训练集里的答案写的都是简短真名**（原流水线的 `patch_sources.py` 就做了剥壳），
# 所以不剥等于**训练与运行时不一致**。
#
# 实测语料文件名只有 4 种形态（90 份里 21 份含《》，另 2 份是「文号--正文」）：
#   关于印发《X》的通知(文号) · 《X》(文号) · 文号--关于印发《X》的通知 · 文号--X
_RE_WRAP_TITLE = re.compile(r"《([^》]+)》")
_RE_DOCNO_PREFIX = re.compile(r"^[^《]*?〔\d{4}〕\s*\d+\s*号\s*[-—–]{1,2}\s*")
MIN_TITLE = 6

# 文件扩展名。**两个语料在来源标题上的口径本来不一致**：规章类文件名不带后缀，
# 而培养方案类工具的 `Source.title` 直接取 `plan_db` 的 `file` 字段
# （`2025级交通工程专业培养方案(主修).md`）—— 后缀于是漏进了用户可见的来源行。
# `.md` 是**内部文件形态**，不是文件真名的一部分，所以在**唯一的标题清洗入口**剥掉。
#
# ★ 只改「用户可见的标题」。内部 `Source.title`、evidence ID（`identity()` 用的是
#   原始 title）、binding、catalog、索引与语料**一律不动** —— 剥的时机在这里，
#   不在数据层。
_RE_FILE_EXT = re.compile(r"\.(?:md|markdown)\s*$", re.IGNORECASE)


def _strip_file_ext(text: str) -> str:
    """剥掉末尾的 `.md` / `.markdown`（大小写不敏感）。剥完为空就当没剥。"""
    return _RE_FILE_EXT.sub("", text).strip() or text


def short_title(src: str) -> str:
    """文档名 → **简短真名**：去掉公文外壳、文号与文件扩展名。

    规则（按实测形态）：
      ① 含《》→ 取**第一个**《》里的内容（4 种外壳里有 3 种都靠这条解决）；
      ② 不含《》但形如 `文号--正文` → 去掉 `文号--` 前缀；
      ③ 其余原样保留（培养方案、章程这类文件名本来就是真名）。
    剥完太短（< 6 字）就当没剥成，原样返回 —— 宁可留着外壳，不可剥成一个残名。
      ④ 最后剥掉末尾的 `.md` / `.markdown` —— 覆盖**普通文件名**与**《标题.md》**两种形态
         （后者由 ① 取出内容后再剥）。这一步在长度门**之后**：扩展名不是「残名」问题。
    """
    s = (src or "").strip()
    m = _RE_WRAP_TITLE.search(s)
    if m and len(m.group(1).strip()) >= MIN_TITLE:
        return _strip_file_ext(m.group(1).strip())
    t = _RE_DOCNO_PREFIX.sub("", s).strip()
    return _strip_file_ext(t if len(t) >= MIN_TITLE else s)


# 路径元素的长度上限。**超过就不是标题，是一整段** —— 实测标题路径元素
# 中位 9 字 / p95 22 字，而最长到 **584 字**（解析层的标题识别把整段当成了标题，
# 语料里实测 94 处）。留着它有两个坏处：用户可见的来源行变成一长串正文；
# 块头被撑长。**只影响显示与块头**，块正文照旧（信息不丢）。
MAX_PATH_CHARS = 40


def clean_path(heading_path, source: str = "") -> list[str]:
    """标题路径 → **能当定位用的那几段**：去掉与文档名重复的、去掉整段式的、去重。

    与 `short_title` 同样是「单一来源」——`build_source_block`（用户可见的来源行）与
    `llm_client.build_messages`（块头）**共用这一个**，不许各写一套。

    实测要处理的两类脏值（都在 `_md` 里量过）：
      · 路径元素就是 md 首行的**通知标题** → 与来源名重复，用户会看到两遍（76 处）；
      · 路径元素是**整段正文**（标题识别误判，94 处）。后者是解析层的缺口，
        本函数只是**不在显示层放大它**，成因见 CLAUDE.md 待办。
    """
    st = short_title(source) if source else ""
    # ⚠️ 容忍**字符串**形态：`heading_path` 的规范形态是列表（Qdrant payload 里就是），
    #    但中间产物可能存成 `" / "` 拼好的字符串 —— 直接 `for e in "第八章…"` 会**逐字符**拆，
    #    块头变成「第 / 八 / 章 / …」（实测踩过，块头被撑到 5960 字）。在这里一次挡掉，
    #    调用方就不必各写一遍 isinstance。
    if isinstance(heading_path, str):
        heading_path = [p.strip() for p in heading_path.split(" / ") if p.strip()]
    out: list[str] = []
    for e in (heading_path or []):
        e = str(e).strip()
        if not e or len(e) > MAX_PATH_CHARS:
            continue
        if st and (e == st or st in e or short_title(e) == st):
            continue                      # 与文档名重复，不显示第二遍
        if e not in out:
            out.append(e)
    return out


# Markdown 标题行 —— 与入库切分器 `indexing._split_headings` **同一形态**
# （`^(#{1,6})\s+\S`），所以「块里有几个标题」的问法与索引侧一致。
_RE_MD_HEADING = re.compile(r"^#{1,6}\s+\S", re.M)


def display_heading_path(heading_path, snippet: str = ""):
    """证据块的**来源行定位**：块里若含**多个** Markdown 条文标题，就不显示条文路径。

    为什么要有这一条（实测，2026-10-17）：`heading_path` 记的是**块的起点**标题，
    而一个块常常横跨好几条（切分器按标题切、短节再合并，实测一份 1856 字的教室
    使用管理办法被切成含「第三条…第六条」的块）。块起点是「第三条 教室使用的原则」，
    回答用的却是「第四条 借用教室需办理的手续」—— 于是来源行指着**错误的条号**。
    这种错误比「不显示条号」**更坏**：它看上去像一条精确的定位。

    ★ 判据只用**已选证据条目自己保存的文本**（`EvidenceLedger` 的 `snippet`）——
      不解析 observation、不重建索引、**不用关键词相似度猜条号**。
    ★ 只有一个标题（或压根没有标题）时**原样返回**：那种情况下块起点就是正确归属。
    ★ 返回原值（可能是 `str`）也不做类型转换 —— `clean_path` 已经容忍字符串形态。
    """
    if len(_RE_MD_HEADING.findall(snippet or "")) > 1:
        return []
    return heading_path

# 模型自写的来源行。行首锚定。
#   前导 `[^\w\s]{0,4}`：覆盖训练集实测的 emoji（📌/📍/🔖）以及 `**` / `>` / `#` / `-`；
#     **「依据资料来源：」这类正文不会被误伤** —— `依据` 是词字符，不属于前导类。
#   中间 `\*{0,2}`：容许 `**资料来源**：` 这种加粗写法。
#   全/半角冒号都收。
#   保留 emoji 兼容：**即使训练集去 emoji 之后**，旧模型输出与线上历史仍可能带 emoji。
_RE_SRC_LINE = re.compile(
    r"^\s*(?:[^\w\s]|[ -㌀\U0001F000-\U0001FAFF]){0,4}\s*"
    r"资料来源\s*\*{0,2}\s*[:：]"
)

# L2 用：引用编号。连写 `[1][2]` 会被逐个命中；另收 `[1,2]` / `[1-3]` 变体。
_RE_CITE_ONE = re.compile(r"[\[【]\s*(\d{1,3})\s*[\]】]")
_RE_CITE_MULTI = re.compile(r"[\[【]\s*(\d{1,3})\s*[,，、]\s*(\d{1,3}(?:\s*[,，、]\s*\d{1,3})*)\s*[\]】]")
_RE_CITE_RANGE = re.compile(r"[\[【]\s*(\d{1,3})\s*[-–~—]\s*(\d{1,3})\s*[\]】]")

# 模型「无信息兜底」的特征词（2026-09-30 新增）。
# 背景：中档（medium）检索会把**低相关**上下文也喂给模型，模型若正确兜底
# （「暂时没查到收录的信息」「手头资料还没同步到」），后端仍会注入来源块 →
# 出现「说没查到却列出几份无关文件」的自相矛盾（实测：校医院怎么报销 → 4 份无关来源）。
# 只取**强兜底词**（正常业务回答几乎不会出现），不用「不清楚」这类弱词防误伤。
_RE_FALLBACK = re.compile(
    r"暂时没|没有收录|没收录|未收录|还没同步|还没更新|没查到|查不到|误导你"
)


def build_source_block(contexts: list[dict]) -> str:
    """从检索上下文生成「资料来源」块（纯文字，无 emoji）。

    按 source 去重、保持采用顺序，保留每个来源实际采用的所有不同 heading_path。
    单来源不编号；`heading_path` 为空则只留《名称》。
    contexts 为空（或全都无 source）时返回 "" —— **不注入**：门控判空时本来就没有来源可标。
    """
    if not contexts:
        return ""
    order: list[str] = []
    paths: dict[str, list[str]] = {}
    for c in contexts:
        src = (c.get("source") or "").strip()
        if not src:
            continue
        if src not in paths:
            order.append(src)
            paths[src] = []
        hp = " / ".join(clean_path(c.get("heading_path"), src))
        if hp and hp not in paths[src]:
            paths[src].append(hp)
    if not order:                       # 全是空 source
        return ""

    def _fmt(src: str) -> str:
        hp = paths.get(src) or []
        # ★ 用**简短真名**：用户看到的是干净的文件名，且与训练集里答案写的来源一致。
        #   （去重仍按**原始 source** 做 —— `paths` 的键是原始名，两件互不干扰。）
        return f"《{short_title(src)}》" + (" " + "；".join(hp) if hp else "")

    if len(order) == 1:
        return f"{SOURCE_PREFIX}{_fmt(order[0])}"
    items = [f"{i}. {_fmt(s)}" for i, s in enumerate(order, 1)]
    return SOURCE_PREFIX + "\n" + "\n".join(items)


# ---------------------------------------------------------------- 正文引用匹配键
# 一份文档名 → 可用于「正文里有没有引用它」的键集合。覆盖 全名 / 短名 / 核心片段：
#   全名   桂林电子科技大学本科生专业分流及转专业管理办法(修订)
#   核心名 本科生专业分流及转专业管理办法        （去「桂林电子科技大学」+ 去尾括号）
#   特征名 本科生专业分流及转专业                （再去通用尾词「管理办法」）
#   专业名 交通工程专业                          （培养方案去「YYYY级…培养方案」）
# ★ 与 `short_title` / `clean_path` 一样是**单一来源**：只此一份，别处不许各写一套。
MIN_KEY = 4                       # 匹配键最短长度（低于它的键太泛，易误命中）
_UNI_PREFIX = "桂林电子科技大学"
_RE_TRAIL_PAREN = re.compile(r"[（(][^）)]*[）)]\s*$")
_RE_YEAR_MAJOR = re.compile(r"^\d{4}级")
# 通用尾词：只含它们等于没指明是哪一份文件，故从特征名里剥掉
_GENERIC_TAILS = ("管理办法", "实施办法", "暂行规定", "实施细则", "管理规定",
                  "工作办法", "工作细则", "评审条件", "培养方案", "章程", "条例",
                  "制度", "办法", "规定", "细则", "规范", "方案", "通知", "条件")


def _strip_generic(s: str) -> str:
    changed = True
    while changed:
        changed = False
        for g in _GENERIC_TAILS:
            if s.endswith(g) and len(s) - len(g) >= 2:
                s = s[: -len(g)]
                changed = True
    return s


def _doc_match_keys(src: str) -> list[str]:
    """文档名 → 匹配键列表（保序去重，只留 ≥MIN_KEY 的）。"""
    st = short_title(src or "")
    if not st:
        return []
    keys: list[str] = []

    def add(k: str) -> None:
        k = (k or "").strip()
        if len(k) >= MIN_KEY and k not in keys:
            keys.append(k)

    add(st)                                              # 全名
    core = _RE_TRAIL_PAREN.sub("", st.replace(_UNI_PREFIX, "")).strip()
    add(core)                                            # 核心名
    add(_strip_generic(core))                            # 特征名
    add(_RE_YEAR_MAJOR.sub("", _strip_generic(core)))    # 专业名（去「YYYY级」）
    return keys


def _referenced_sources(body: str, contexts: list[dict]) -> list[dict]:
    """来源过滤终版：只保留**正文实际引用到**的文档；**零匹配不注入**（宁缺勿错）。

    背景：`build_source_block` 会把上下文里每一份文档全部列出（去重），而中档
    （medium）上下文可能躺几个低相关干扰块（实测「大学英语成绩换算」被 3 份培养方案
    『教学进程计划表』混入，rr 仅差 0.011~0.020）→ 末尾来源比实际用的多。

    ★ 2026-10-01 改版：删掉「零匹配保守保留 top-1」的兜底。理由 ——
      那个兜底正是"多列"的来源（模型只用了 A，却把最相关的 B 也标上）；
      而「宁缺勿错」要求：判不出引用了哪份，就**一份都不标**，不猜。

    匹配键（`_doc_match_keys`）：**全名 / 去「桂林电子科技大学」前缀的核心名 /
    去通用尾词的特征名 / 去「YYYY级」的专业名**（覆盖 全名·短名·核心片段）。
    ★ 扫描对象是**模型完整输出**（含它自写的来源行）——模型自己点名的文档算"引用到"。
      只扫正文时命中率极低（语料注记：模型正文写的是泛称/指代），实测对比见 318 副作用报告。
    """
    if not contexts or not body:
        return []
    hits = []
    for c in contexts:
        keys = _doc_match_keys(c.get("source") or "")
        if any(k in body for k in keys):
            hits.append(c)
    return hits


def _last_src_line_offset(text: str) -> int:
    """模型自写来源行**最后一次**出现处的偏移；没有则返回 -1。

    与 `finalize_answer` 必须同口径（**取最后一条**、都用行首锚定的 `_RE_SRC_LINE`）——
    两处一旦不一致，流式截断点与最终替换点就会错位，用户会看到来源块闪现。
    """
    off, last = 0, -1
    for ln in (text or "").split("\n"):
        if _RE_SRC_LINE.match(ln):
            last = off
        off += len(ln) + 1
    return last


def _strip_sources_with_flag(text: str) -> tuple[str, bool]:
    """剥掉模型自写来源行的**唯一实现**；同时给出「是否剥过」。

    ★ `strip_model_sources`（公开）与 `finalize_answer`（运行时）**共用这一个**——
      两处各写一套的话，流式截断点与最终替换点会错位（`streamable_cut` 已经因为
      同口径问题被钉过一次），所以这里不复制第二份。
    """
    lines = (text or "").split("\n")
    last_idx = -1
    for i, ln in enumerate(lines):
        if _RE_SRC_LINE.match(ln):
            last_idx = i
    if last_idx < 0:
        return (text or "").rstrip(), False
    return "\n".join(lines[:last_idx]).rstrip(), True


def strip_model_sources(text: str) -> str:
    """剥掉模型自写的「资料来源」行：从**最后一条**该行起整段截断，再 `rstrip`。

    与 `finalize_answer` 的剥离行为**逐字一致**（同一个实现）。
    正文里「依据资料来源：」不会被误伤 —— `_RE_SRC_LINE` 行首锚定，
    且 `依据` 是词字符、不属于允许的前导类。
    """
    return _strip_sources_with_flag(text)[0]


def streamable_cut(full: str, emitted: int) -> int:
    """流式过程中**可以安全发出**的前缀长度（`chat.py` 每收到一个 delta 调一次）。

    只扣一处：从模型自写的来源行开始往后都不发 —— 那一段会被 `finalize_answer`
    整段替换，先发出去只会闪烁一下。其余内容**原样立刻发出**（这正是原来
    `TAIL_HOLD` 时代做不到的：扣住的恒比答案还长，于是一个字都没流出来）。

    ⚠️ 返回值恒 ≥ `emitted` —— 已发出去的前缀绝不回收（SSE 收不回来）。
       即使多发了（比如来源行紧随其后才出现）也无妨：流末尾的
       `{"replace": ...}` 末事件会把前端气泡整体覆盖成最终形态。
    """
    i = _last_src_line_offset(full)
    if i < 0:
        return len(full)          # 还没有来源行 → 全部可发
    if i >= emitted:
        return i                  # 从来源行起扣住；`i == emitted` 时即"一个字都不发"
    return len(full)              # 来源行已经在外面了 → 收不回来，照发，由末事件兜底


# ---------------------------------------------------------------- 重复退化护栏
# 实测（318 生产同源跑批）：**4.1% 的答案尾部陷入复读**，且会把 `max_tokens` 吃满
# （实测 6.5% 的答案 `finish_reason=length`）。典型形态是同一句建议被连发十几遍：
#   「所以具体怎么排最稳妥，还是得问下咱们学院的教学秘书或者班主任…」×13
# `repetition_penalty=1.05` 拦不住它 —— 复读出现在模型"不确定、走兜底"的时候。
#
# 为什么护栏放在这里（而不是采样参数）：调高惩罚会连带损伤正常输出，而**复读有确定性的
# 形态**（一段文本连续原样重复 3 次以上），可以精确检测、精确截断。
# ★ 必须在**流式过程中**也判一次并提前 break：否则要等它把 512 token 复读完，
#   用户白等十几秒才看到一段被截断的废话。
DEGEN_MIN_BLOCK = 24      # 重复块最短 24 字 —— 短于此的正常重复（如编号「1.」）不判
DEGEN_MAX_BLOCK = 300
# ★ 2026-10-02：这里原先挂着一条不变式 `3*DEGEN_MAX_BLOCK = 900 < TAIL_HOLD = 2000`，
#   用来保证「护栏的截断点一定落在还没发出去的那段尾巴里」。TAIL_HOLD 已删除，
#   该不等式随之作废 —— 一致性改由流末尾的 `{"replace": ...}` 末事件保证，
#   与截断点落在哪无关。护栏**仍然必须在流式过程中提前 break**（见下），
#   否则要白等它把 512 token 复读完，用户才看到一段被截断的废话。


def find_degeneration(text: str) -> int:
    """检出「重复退化」→ 返回**截断位置**；没退化返回 -1。

    判据：末尾存在一段（24~300 字）**连续原样重复 ≥3 次**。取**最小的**重复块，
    这样截断点尽量靠前（保留第一次出现，丢掉后续复读）。

    为什么是 3 次而不是 2 次：2 次在正常中文里会出现（排比、对仗、表格里相邻两行相同），
    3 次连续逐字相同则几乎只可能是退化 —— 宁可漏判，不可误伤正文。
    """
    t = (text or "").rstrip()
    n_len = len(t)
    if n_len < DEGEN_MIN_BLOCK * 3:
        return -1
    for n in range(DEGEN_MIN_BLOCK, min(DEGEN_MAX_BLOCK, n_len // 3) + 1):
        blk = t[-n:]
        if not blk.strip():
            continue
        if not t.endswith(blk + blk):
            continue
        # ★ 必须**数清末尾连续重复了几次**，不能只看是否重复 2 次。
        #   只看 2 次的话，"保留最后 3 次之前的内容"会连着留下前面几次复读
        #   （实测 5 次复读时留下 2 份 —— 单测当场抓住）。这里回溯到该段的起始，
        #   只保留**第一次出现**。
        k, pos = 0, n_len
        while pos - n >= 0 and t[pos - n:pos] == blk:
            k += 1
            pos -= n
        if k >= 3:
            return pos + n
    return -1


def strip_degeneration(text: str) -> tuple[str, bool]:
    """截掉尾部的重复退化。返回 (新文本, 是否截过)。"""
    cut = find_degeneration(text)
    if cut < 0:
        return text, False
    return text[:cut].rstrip(), True


def finalize_answer(full: str, contexts: list[dict], tier: str = "medium") -> tuple[str, bool]:
    """整篇回答的**最终形态**：剥离模型自写来源（含多段）→ 追加真实来源块。

    ★ 2026-09-30 修复：v3 模型会在答案末尾输出**多段**自写来源
      （条号拼接段 + 模仿【参考资料】块的块头列表段），早期的「只剥尾部窗口里的
      第一行」写法剥不干净 → 出现两个「资料来源：」。
      改为：全文找**最后一条**模型来源行，从那里截断，整体替换为后端注入的真实来源块。
      正文里「依据资料来源：」不会被误伤（_RE_SRC_LINE 行首锚定 + 词字符前导排除）。

    ★ 2026-09-30 双保险（来源注入与「检索档位」联动）：
      high 档（best ≥ rerank_high，来源真实相关）→ **无条件注入**；
      medium 档（灰色地带，文档相关性存疑）→ **仅当模型未兜底**才注入 ——
        兜底关键词检测只是辅助（模型换措辞可能漏），真正的防线是档位：
        中档宁可少注入，也不可把低相关文档写成「资料来源」误导用户；
      low 档：contexts 已为空，到不了这里。

    ★ 这是**落库内容**的唯一来源。流式发出去的内容以它为准：
      `chat.py` 在流末尾发一条 `{"replace": <本函数结果>}` 末事件，前端整体覆盖气泡 ——
      所以「用户最终看到的 = 落库的」由**末事件**保证，不再依赖「哪些字已经发出去了」。
      而**落库在断流时也调用它**，所以中途断开不会丢掉尾巴处理（落库与发送解耦）。
    """
    if not full:
        return full, False
    # ★ 先截掉尾部复读，再做剥壳与注入 —— 顺序朝前，后面全部基于干净文本。
    full, _degen = strip_degeneration(full)
    if not full:
        return full, False
    # ★ 剥离逻辑与公开的 `strip_model_sources` **共用同一个实现**，不复制第二份。
    body, stripped = _strip_sources_with_flag(full)
    # ★ 2026-10-01：来源过滤终版 —— 只注入**模型输出里确实引用到**的文档；零匹配不注入。
    #   匹配扫的是 `full`（含模型自写来源行）而不是 `body`：模型自写的来源行是它
    #   「用了哪份」的最强信号；只扫剥壳后的正文，命中率极低（正文多是泛称/指代）。
    verified = [c for c in contexts if c.get("citation_verified")]
    block = build_source_block(verified if verified else _referenced_sources(full, contexts))
    if not block:
        return body, stripped
    # ★ 2026-09-30：双保险 —— 高相关档无条件注入（来源真实相关）；
    #   中档（灰色地带）仅在模型未兜底时注入，避免「说没查到却列无关文件」。
    if tier != "high" and _RE_FALLBACK.search(body):
        return body, stripped
    return f"{body}\n\n{block}", stripped


# ---------------------------------------------------------------- L2：[n] 越界校验
def check_citations(text: str, n_refs: int) -> list[dict]:
    """检查 `[n]` 越界（n 超出本次实际提供的参考资料条数）。

    ✅ **已接入运行时**（2026-09-28）—— 但接的是**观察型**：只打服务端日志，
      **不改答案**。原因见 `violation_summary` 的说明（要保住「流出=落库」不变量）。
      当初「先不接线」等的就是「模型到底吐不吐 [n]」这个实测，接线正是为了把观测点装上。

    ⚠️ `n_refs` 必须用 **build_messages 里 refs_list 的实际长度**，不是 top_k ——
      `MAX_CONTEXT_CHARS` 硬闸门会让实际条数**少于** top_k，拿 top_k 当 N 会漏判。
      `chat.py` 传的是 `len(contexts)`（= 实际喂进去的条数）。

    已知误报源，按此排除：markdown 链接 `[1](url)`、表格行（`|` 开头）内的 `[1]`。
    另实测确认**公文文号不会误报**：`[2019]` / `【2019】`（4 位数字）不被 `\\d{1,3}` 匹配，
    `（教高【2019）6号）` 因为括号不配对也不匹配 —— 这条很关键，语料里文号遍地都是。
    """
    out: list[dict] = []
    if not text:
        return out
    masked: list[tuple[int, int]] = []
    for m in _RE_CITE_ONE.finditer(text):
        if text[m.end():m.end() + 1] == "(":                       # markdown 链接
            masked.append(m.span())
            continue
        line_start = text.rfind("\n", 0, m.start()) + 1
        if text[line_start:m.start()].lstrip().startswith("|"):    # 表格行
            masked.append(m.span())
            continue
        n = int(m.group(1))
        if n < 1 or n > n_refs:
            out.append({"kind": "out_of_range", "n": n, "n_refs": n_refs,
                        "span": list(m.span()), "text": m.group(0)})
    for rx in (_RE_CITE_RANGE, _RE_CITE_MULTI):
        for m in rx.finditer(text):
            if any(a <= m.start() < b for a, b in masked):
                continue
            nums = [int(x) for x in re.findall(r"\d{1,3}", m.group(0))]
            if rx is _RE_CITE_RANGE and len(nums) == 2:
                nums = list(range(nums[0], nums[1] + 1))    # [1-3] → 1,2,3
            bad = [n for n in nums if n < 1 or n > n_refs]
            if bad:
                out.append({"kind": "out_of_range", "n": bad[0], "n_refs": n_refs,
                            "span": list(m.span()), "text": m.group(0)})
    return out


def violation_summary(violations: list[dict]) -> str:
    """把越界结果压成**一行日志**（`routers/chat.py` 打点用）。无越界返回空串。

    为什么一直是「观察型接线」而不是「改答案」—— 原先 `chat.py` 有一条**不变量**：
    已 SSE 发出的前缀与最终落库内容**逐字相同**（靠尾部扣住 2000 字实现）。
    越界标记可能出现在**文中任意位置**，要在那里改 `[n]` 就必须改已发出的前缀，
    不变量当场破掉（前端看到的和库里存的不一致）。

    ★ 2026-10-02：那条不变量已随 TAIL_HOLD 一起消失，一致性改由流末尾的
      `{"replace": ...}` **末事件**保证 —— 于是**技术上现在可以直接改写了**
      （改了也会被末事件覆盖回前端，两边始终一致）。但本函数仍**保持只观测**：
      实测模型吐 `[n]` 是 0/688，没有要修的对象；观测点先留着，真出现越界再说。
    """
    if not violations:
        return ""
    bad = sorted({v["n"] for v in violations})
    return (f"[n] 引用越界 {len(violations)} 处：n={bad} 超出本次实际参考资料条数 "
            f"n_refs={violations[0]['n_refs']}；首次出现 {violations[0]['text']!r}")
