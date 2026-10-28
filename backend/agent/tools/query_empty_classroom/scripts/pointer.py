# -*- coding: utf-8 -*-
"""空教室查询 —— **指引卡，不是真查询**（2026-10-10 用户拍板）。

## 为什么是「指路」而不是「代查」

真接教务系统那条路**探测完了但走不通**（记在
`docs/superpowers/plans/2026-10-10-tool-calling-lora-plan.md` 的「空教室接口」一节）：

- **接口层已完全攻克** —— `POST /student/ws/room-borrow/free-list` 的请求体、枚举来源、
  返回结构全都拿到了，用用户手动提供的 cookie 实测出 **142 间空教室**
- **卡在会话层** —— 教务系统**在任何路径上都不消费 CAS 票据**（8 条回调路径全试过），
  CAS 认证成功但拿到的 `SESSION` 打不通接口；它的会话从哪来，现有证据定不了

所以在会话问题解决之前，这张卡**如实说「我查不了实时的」并给指路**。

## 为什么值得做成工具（而不是让模型凭记忆答）

**URL 必须从代码出来**。放进提示词或训练答案里让模型背，它就会**编** ——
编出来的教务系统网址用户点进去是 404 或者更糟。这里 URL 是常量，
模型只负责把它读进答案，零编造空间。

## 它**不**做什么

- 不做任何网络请求（`cas.py` 那条真接路径**没有接线**，别以为这里会去登教务系统）
- 不查语料。教室**借用/使用规则**是规章制度里真有依据的（《桂林电子科技大学教室使用
  管理办法》桂电教〔2015〕67号），那类问题走 `search_regulations`，不走这里。
"""

# ★ 单一来源：真接落地后，这里换成真查询，**schema 与样本形态不用动**。
ENTRY_URL = "https://bkjwtest.guet.edu.cn/student/home"
ROOM_FREE_PATH = "/student/for-std/room-free"
NEED = "学号 + 统一身份认证密码"


def query_empty_classroom() -> dict:
    """返回指路信息。**无参数** —— 它不查任何东西，要日期/节次也没用。

    ⚠️ 刻意**不设 `when` 之类的参数**：那会造出「模型传了时间、工具却忽略它」的假象。
    真接落地时再加，那时它才真的会被用上。
    """
    return {"kind": "pointer", "entry_url": ENTRY_URL, "room_free_path": ROOM_FREE_PATH,
            "need": NEED, "reason": "realtime_requires_login"}