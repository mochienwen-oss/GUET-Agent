# dedupe_corpus.py —— 找出**同一份校规被存了多份**的重复文件
#
# 为什么需要它：audit_corpus.py 的查重靠「归一化文件名 + sha1 内容精确指纹」，
# 而实际语料里的重复是**文件名半角/全角括号不同**、且正文里文号带空格
# （`(桂电教〔2021〕6号)` vs `（桂电教〔2021〕6 号）`），精确指纹一个都抓不到，
# 于是审计报「没发现重复」——假阴性。实测 127 份语料里混了 14 组重复。
#
# 这些重复不是小事：检索时候选池里会出现同一份文档的两三个副本，rerank 分数几乎相同
# （实测差 0.0006），于是整个 top-k 被一份文档吃光，而 Recall@1 因为「期望文档确实在
# 结果里」完全看不出问题。
#
# 判重键 = **发文机关 + 年份 + 序号**（桂电教〔2025〕28号）。这是校规的稳定唯一标识，
# 比文件名相似度、比内容哈希都可靠。
#
# 用法（在项目根目录执行）：
#   python scripts/dedupe_corpus.py                # 只列清单，不动任何文件
#   python scripts/dedupe_corpus.py --apply        # 把重复的移到 _backup/dedupe/（可回滚）
#
# 保留规则：① 正文更全的优先（实测差异组里，大者是「多了发文通知外壳」的超集，
#            小的被覆盖 96~100%）；② 一样全时，文件名无多余空格的优先。

import argparse
import collections
import os
import re
import shutil
import sys
import unicodedata

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 默认只扫「真正会被灌库」的三个目录（与部署 SOP 装配 _kb 的来源一致）。
# 注意不要把 _md/培养方案 和 _md_fixed/培养方案 一起传进来——它们是新旧两条
# 管线的同一批产物（各 63 份），混着灌会让整个培养方案家族翻倍。
DEFAULT_ROOTS = ["_md_fixed/培养方案", "_md/规章制度", "_md/研究生院"]

# 发文机关 + 六角/方括号 + 年份 + 序号 + 号
_DOC_NO = re.compile(r"([一-龥]{1,6}?)[〔\[]\s*(\d{4})\s*[〕\]]\s*(\d{1,3})\s*号")


def _read_text(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def doc_key(filename: str):
    """从文件名取判重键：(发文机关, 年份, 序号)。取不到就返回 None（不参与判重）。"""
    m = _DOC_NO.search(unicodedata.normalize("NFKC", filename))
    return (m.group(1), m.group(2), m.group(3)) if m else None


def _norm_body(text: str) -> str:
    """正文归一化：NFKC + 去掉所有空白。半角/全角和换行差异都不该影响判等。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _has_stray_space(filename: str) -> bool:
    """文件名里文号带空格（`桂电教〔2021〕32 号`）——排版噪音，不优先保留。"""
    return bool(re.search(r"\d\s+号", unicodedata.normalize("NFKC", filename)))


def collect(roots):
    groups = collections.defaultdict(list)
    for r in roots:
        base = r if os.path.isabs(r) else os.path.join(ROOT, r)
        if not os.path.isdir(base):
            print(f"⚠️  跳过不存在的目录：{r}")
            continue
        for dp, _dirs, files in os.walk(base):
            for f in files:
                if not f.endswith(".md"):
                    continue
                k = doc_key(f)
                if k:
                    groups[k].append(os.path.join(dp, f))
    return groups


def build_plan(roots):
    groups = collect(roots)
    plan = []
    for k, paths in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][2])):
        if len(paths) < 2:
            continue
        items = []
        for p in paths:
            body = _norm_body(_read_text(p))
            items.append({"path": p, "n": len(body), "hash": hash(body),
                          "stray": _has_stray_space(os.path.basename(p))})
        # 正文多的优先；一样多时，文件名没多余空格的优先
        items.sort(key=lambda x: (-x["n"], x["stray"]))
        identical = len({i["hash"] for i in items}) == 1
        plan.append({
            "key": f"{k[0]}〔{k[1]}〕{k[2]}号",
            "note": "正文完全相同（任删其一，无损）" if identical else "保留者含发文通知外壳（是超集）",
            "keep": items[0]["path"],
            "delete": [i["path"] for i in items[1:]],
        })
    return plan


def main():
    p = argparse.ArgumentParser(description="查找同一份校规的重复文件（按发文机关+年份+序号判重）")
    p.add_argument("--roots", nargs="+", default=DEFAULT_ROOTS, help="要扫描的 md 目录")
    p.add_argument("--apply", action="store_true",
                   help="把重复文件移到 _backup/dedupe/（默认只列清单，不动文件）")
    args = p.parse_args()

    plan = build_plan(args.roots)
    n_del = sum(len(x["delete"]) for x in plan)

    if not plan:
        print("✅ 没发现按文号判定的重复")
        return

    print(f"\n{'=' * 92}")
    print(f"重复文档：{len(plan)} 组 / 共 {len(plan) + n_del} 份  →  保留 {len(plan)} 份，处理 {n_del} 份")
    print(f"{'=' * 92}\n")
    for x in plan:
        print(f"● {x['key']}　{x['note']}")
        print(f"    ✅ 保留  {os.path.relpath(x['keep'], ROOT)}")
        for d in x["delete"]:
            print(f"    ❌ 重复  {os.path.relpath(d, ROOT)}")
        print()

    if not args.apply:
        print(f"（未改动任何文件。确认后加 --apply 把上面 {n_del} 份重复移到 _backup/dedupe/）")
        return

    dest_root = os.path.join(ROOT, "_backup", "dedupe")
    moved = 0
    for x in plan:
        for d in x["delete"]:
            rel = os.path.relpath(d, ROOT)
            dst = os.path.join(dest_root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst):
                print(f"⚠️  目标已存在，跳过：{rel}")
                continue
            shutil.move(d, dst)
            moved += 1
    print(f"✅ 已移走 {moved} 份重复文件 → {os.path.relpath(dest_root, ROOT)}")
    print("   回滚：把 _backup/dedupe/ 下的文件按原路径移回即可。")
    print("   下一步：重新灌库（scripts/ingest_local.py --sync ...）让向量库与语料一致。")


if __name__ == "__main__":
    main()