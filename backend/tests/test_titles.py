# tests/test_titles.py —— 会话标题的规则层单测（纯函数，不需要 LLM/DB/Qdrant）
#
# 这一层存在的理由见 `titles.py` 顶部：标题实测有两个硬伤 ——
#   ① 侧边栏只看得见约 15 个字，而正文切的是 30 字（后一半永远看不到）；
#   ② 前 15 个字被「请教一下，」这类开场白占满，444 题里 17% 的标题前 6 字雷同。
# 规则负责把标题**变短变干净**（读主题交给模型，见 llm_client.generate_title）。
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conversations import titles as T  # noqa: E402

_EVAL = Path(__file__).resolve().parents[2] / "测试集" / "eval_v1.jsonl"


# ---------------------------------------------------------------- 清洗
def test_tidy_strips_lead_pleasantries():
    """开场白不携带主题信息，却占着标题最贵的位置 —— 必须剥掉。"""
    for q, want in [
        ("请问一下，转专业需要什么条件？", "转专业需要什么条件"),
        ("请教一下大学英语四级考试考到多少分", "大学英语四级考试考到多少分"),
        ("我想问下，奖学金多久评一次", "奖学金多久评一次"),
        ("麻烦问一下，宿舍报修找谁", "宿舍报修找谁"),
        # 人称也要一起剥：否则剩下「学长，都有哪些情形…」
        ("想问问学长，都有哪些情形不得转学", "都有哪些情形不得转学"),
    ]:
        assert T.tidy(q) == want, (q, T.tidy(q))


def test_tidy_strips_school_modifier_but_not_the_university_name():
    """「咱们学校的 X」→「X」；但**不能**碰「桂林电子科技大学」——里面有「大学」不是「学校」。"""
    assert T.tidy("咱们学校的通识教育专家委员会是啥") == "通识教育专家委员会是啥"
    assert T.tidy("咱学校教师系列的高级职称评审分几种") == "教师系列的高级职称评审分几种"
    assert "桂林电子科技大学" in T.tidy("桂林电子科技大学的校训是什么")


def test_tidy_strips_tail_particles_and_punctuation():
    """模型输出里实测漏出过「…得分规则**哈～**」—— 训练里学到的答题语气。"""
    assert T.tidy("专升本学制是几年呀？") == "专升本学制是几年"
    assert T.tidy("黄色预警的条件是什么哈～") == "黄色预警的条件是什么"
    assert T.tidy("本科课堂包括哪两类？ ") == "本科课堂包括哪两类"


# ---------------------------------------------------------------- 占位标题
def test_placeholder_never_exceeds_limit():
    q = "在实验技术系列职称评审中，一年的专职辅导员工作经历能代替班主任经历吗？"
    t = T.placeholder_title(q)
    assert len(t) <= T.TITLE_MAX, (len(t), t)


def test_placeholder_prefers_breaking_at_punctuation():
    """能退到标点就退，别切出半句；但**回退不超过 3 字**。"""
    # 退 1 字到问号前 → 干净的话题短语
    assert T.placeholder_title("学科学术委员会的届期是几年？可以连任吗？") == "学科学术委员会的届期是几年"
    # 退 3 字到「和」→ 正是一个完整短语，该收
    assert T.placeholder_title("师资博士后和项目博士后的在站工作期限一样吗？") == "师资博士后和项目博士后"
    # ★ 不能一路退：退 5 字会得到 9 字的「进站做博士后对年龄」，比硬切丢的信息更多
    got = T.placeholder_title("进站做博士后对年龄和获博士学位年限有什么要求？")
    assert len(got) > 9, got


def test_placeholder_short_question_untouched():
    assert T.placeholder_title("本科课堂包括哪两类？") == "本科课堂包括哪两类"
    assert T.placeholder_title("专升本学制几年") == "专升本学制几年"


def test_placeholder_empty_input():
    assert T.placeholder_title("") == ""
    assert T.placeholder_title("   ") == ""
    # 全是开场白、剥完为空 → 退回原问题的截断，而不是给个空标题
    assert T.placeholder_title("请问") == "请问".strip()


# ---------------------------------------------------------------- 要不要叫模型
def test_needs_model_skips_short_and_clean():
    """清洗完就很短的题**直接跳过** —— 实测题面 p50 才 20 字，这类题占位标题本来就够好。"""
    assert not T.needs_model("专升本学制几年")
    assert not T.needs_model("本科课堂包括哪两类？")


def test_needs_model_asks_for_long_or_colloquial():
    assert T.needs_model("在实验技术系列职称评审中，一年的专职辅导员工作经历能代替班主任经历吗？")
    assert T.needs_model("社科系列研究员和副研究员对权威刊物论文的篇数要求分别是什么？")
    # 短但口语化、剥完仍超限 → 也该问
    assert T.needs_model("咱们学校的通识教育专家委员会到底是个啥机构呀？")


# ---------------------------------------------------------------- 洗模型输出
def test_clean_model_title_truncates_and_cleans():
    """模型**不可信地遵守格式**（实测约一半超过 12 字、会漏语气词）—— 这里全兜住。"""
    assert T.clean_model_title("博士后年龄和年限要求") == "博士后年龄和年限要求"
    assert T.clean_model_title("黄色预警的触发条件哈～") == "黄色预警的触发条件"
    assert T.clean_model_title("「专升本学制」") == "专升本学制"
    assert T.clean_model_title("大学英语四级考试（CET4）认定分数要求") == "大学英语四级考试（CET4）认定"[: T.TITLE_MAX]
    # 长度一律被压回上限
    assert len(T.clean_model_title("社会科学研究系列研究员、副研究员权威刊物论文篇数要求")) <= T.TITLE_MAX


def test_clean_model_title_takes_first_line_only():
    assert T.clean_model_title("宿舍热水供应时间\n（这是标题）") == "宿舍热水供应时间"


def test_clean_model_title_drops_source_leak():
    """万一它把训练习惯带出来（把「资料来源：《X》」当标题），从该处截断。"""
    assert "资料来源" not in T.clean_model_title("转专业条件 资料来源：《本科生转专业管理办法》")


def test_clean_model_title_rejects_unusable():
    """洗成空/一个字 → 返回空串，调用方据此**保留占位标题**。"""
    assert T.clean_model_title("") == ""
    assert T.clean_model_title("   ") == ""
    assert T.clean_model_title("嗯") == ""
    assert T.clean_model_title("哈～") == ""
    assert T.clean_model_title("？") == ""


# ---------------------------------------------------------------- 真实分布回放
def test_replay_eval_questions_all_titles_well_formed():
    """在真实 444 题上回放：占位标题必须**条条可用**。

    这是这一层唯一有意义的验收 —— 单看几条手工样例看不出「17% 前 6 字雷同」
    这种分布性问题（那正是改版前的实测值）。
    """
    if not _EVAL.is_file():
        import pytest
        pytest.skip("测试集/eval_v1.jsonl 不在，跳过回放")
    qs = [json.loads(l)["question"] for l in open(_EVAL, encoding="utf-8") if l.strip()]
    assert len(qs) >= 400
    bad_len = [t for t in (T.placeholder_title(q) for q in qs) if len(t) > T.TITLE_MAX]
    assert not bad_len, f"有 {len(bad_len)} 条超长：{bad_len[:3]}"
    titles = [T.placeholder_title(q) for q in qs]
    lead = ("请问", "请教", "我想问", "想问", "麻烦问", "我就是", "想问问")
    bad_lead = [t for t in titles if t.startswith(lead)]
    assert not bad_lead, f"有 {len(bad_lead)} 条仍以口语开场白开头：{bad_lead[:3]}"
    bad_tail = [t for t in titles if t.endswith(("？", "?", "。", "，", "、", "！", "哈", "呀", "啊"))]
    assert not bad_tail, f"有 {len(bad_tail)} 条以标点/语气词收尾：{bad_tail[:3]}"
    assert not [t for t in titles if not t], "有空标题"