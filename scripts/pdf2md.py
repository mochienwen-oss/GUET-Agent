# pdf2md.py —— 批量把 PDF/DOCX/HTML 转成 Markdown（复用 backend/mdify.py）
#
# 与后端上传走的是**同一条转换路径**，所以本地转出来的质量 == 线上上传的质量。
# 转换前会做 NFKC 归一化（修「康熙部首」污染）并清网页页眉页脚噪音，见 mdify._normalize。
#
# 用法（在项目根目录执行）：
#   # 只体检、不写盘：看每个文件的字数 / 建出来的「第X章」「第X条」数量
#   python scripts/pdf2md.py guet数据文件/规章制度
#
#   # 真正转换（保留子目录结构，子目录名会成为知识库分类）
#   python scripts/pdf2md.py guet数据文件/规章制度 _md/规章制度
#
#   # 只转部分子目录
#   python scripts/pdf2md.py guet数据文件/规章制度 _md/规章制度 --only 人事制度 科研制度
#
# 判断标准：
#   ✅ 正文字数 ≥ 200   → 转换成功，可以入库
#   ⚠️ 正文字数 < 200   → 多半是扫描件（无文字层），需要 OCR / MinerU，pypdf 救不了

import argparse
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND_DIR)

from documents import mdify  # noqa: E402

_EXTS = (".pdf", ".docx", ".html", ".htm", ".txt", ".md")
# ⚠️ 判据已收口到 mdify.MIN_DOC_CHARS / MIN_CHARS_PER_PAGE（与 audit_parse.py、
#    seed_knowledge.py 共用），这里不再自己写一份，免得改一处漏一处。

# 数一数转换后能建出多少标题层级 —— 直接决定父子分块的质量
_RE_CHAP = re.compile(r"^\s*#{0,6}\s*第[一二三四五六七八九十百零]+[章篇部分]", re.M)
_RE_ART = re.compile(r"^\s*#{0,6}\s*第[一二三四五六七八九十百零]+条", re.M)


def _clean_stem(stem: str) -> str:
    """文件名归一：NFKC 修康熙部首 + 去掉多余空格。

    **文件名本身也会带污染**（例如「关于印发《桂林电⼦科技⼤学⼯程类硕⼠ 专业学位论⽂基本要求》的」），
    文件名字符串会成为知识库里的文档标题，既要能显示正常，也要能被检索和评测对上。
    另外去掉「通识教育 专家委员会章程」这类夹在标题中间的空格，否则和测试集里的写法对不上。
    """
    import unicodedata
    fixed = unicodedata.normalize("NFKC", stem)
    return re.sub(r"\s+", "", fixed) or stem


def collect(src: str, only: list | None) -> list[tuple[str, str]]:
    """返回 [(文件绝对路径, 相对子目录)]；only 用于只处理指定的一级子目录。"""
    if os.path.isfile(src):
        return [(src, "")]
    items: list[tuple[str, str]] = []
    for dirpath, _dirs, names in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        top = rel.split(os.sep)[0] if rel != "." else ""
        if only and top not in only:
            continue
        for n in sorted(names):
            if n.lower().endswith(_EXTS):
                items.append((os.path.join(dirpath, n), "" if rel == "." else rel))
    return items


def main():
    p = argparse.ArgumentParser(description="批量把文档转成 Markdown（复用 backend/mdify.py）")
    p.add_argument("src", help="源目录或单个文件")
    p.add_argument("dst", nargs="?", default=None, help="输出目录；不填 = 只体检不写盘")
    p.add_argument("--only", nargs="*", default=None, help="只处理这些一级子目录")
    p.add_argument("--force", action="store_true",
                   help="即使新结果远短于已有 md 也覆盖。默认跳过——"
                        "扫描件的文字层会抽出几行碎片，覆盖掉已有的好数据是静默事故")
    p.add_argument("--shrink-ratio", type=float, default=0.6,
                   help="新结果短于已有 md 的这个比例就跳过（默认 0.6）")
    p.add_argument("--no-image-ocr", action="store_true",
                   help="跳过正文插图的 OCR。默认**开启**——实测培养方案里"
                        "「图形版课表」有 358~553 个汉字，只有 OCR 能提取")
    args = p.parse_args()

    items = collect(args.src, args.only)
    if not items:
        print(f"❌ {args.src} 下没有可转换的文件")
        return

    print(f"共 {len(items)} 个文件　→　{'写盘到 ' + args.dst if args.dst else '仅体检（不写盘）'}")
    print("-" * 96)

    ok = bad = rejected = 0
    written = []
    rejects = []
    for path, rel in items:
        name = os.path.basename(path)
        try:
            with open(path, "rb") as f:
                # ★ 用 to_markdown_report 而不是 to_markdown：质量门需要 stats
                #   （页数、哪些页是扫描页），只看 md 字数看不出「20 页只出了 500 字」。
                md, stats = mdify.to_markdown_report(name, f.read(),
                                                     ocr_images=not args.no_image_ocr)
        except Exception as e:
            print(f"❌ 转换失败  {rel}/{name[:60]}  →  {str(e)[:60]}")
            bad += 1
            continue

        n_chap, n_art = len(_RE_CHAP.findall(md)), len(_RE_ART.findall(md))
        # ★ 入库前置质量门（用户决定：不合格的**拒绝入库**）。
        #   在这里拦是因为只有这道关能拿到**源文件的页数/扫描页信息**——
        #   到了 seed_knowledge 那一层只剩 md 文本，判不出「几页里出了多少字」。
        #   坏 md 根本不写进 _md/，也就进不了 _kb/ 和向量库。
        reasons = mdify.quality_reasons(md, pages=stats.get("pages"), stats=stats)
        if reasons:
            rejected += 1
            rejects.append((f"{rel}/{name}", reasons))
            print(f"⛔ {len(md):7d} 字　拒绝写盘　{rel}/{name[:52]}")
            for r in reasons:
                print(f"      · {r}")
            continue

        print(f"✅ {len(md):7d} 字　章{n_chap:2d}　条{n_art:3d}　"
              f"{rel}/{name[:58]}")

        ok += 1
        if args.dst:
            out = os.path.join(args.dst, rel, _clean_stem(os.path.splitext(name)[0]) + ".md")
            # ★ 另一道护栏：不许用差数据覆盖好数据。
            #   扫描件的 PDF 里往往残留几行文字层碎片（封面/页眉），能过 200 字门槛，
            #   但拿它覆盖掉 OCR 辛苦转出来的完整正文就是静默事故。
            if os.path.exists(out) and not args.force:
                old_len = len(open(out, encoding="utf-8", errors="ignore").read().strip())
                if old_len and len(md.strip()) < old_len * args.shrink_ratio:
                    print(f"   ⏭️  跳过写入：新结果 {len(md)} 字 远短于现有 {old_len} 字"
                          f"（扫描件文字层碎片？加 --force 可强制覆盖）")
                    ok -= 1
                    continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w", encoding="utf-8") as fw:
                fw.write(md)
            written.append(out)

    print("-" * 96)
    print(f"正常 {ok} 个，疑似扫描件/抽取失败 {bad} 个，**拒绝写盘 {rejected} 个**")
    if written:
        print(f"已写入 {len(written)} 个 md → {args.dst}")
    if rejects:
        print(f"\n⛔ 以下 {len(rejects)} 份被判不合格、**没有写盘**（会汇总在下面）")
    if bad:
        print("\n⚠️  上面标 ⚠️ 的是**没有文字层**的扫描件，抽不出内容，需要整页 OCR。")
        print("    走 scripts/ocr2md.py（本地 CPU，无需外网）：")
        print("      PYTHONIOENCODING=utf-8 python scripts/ocr2md.py --check <文件>   # 先逐页体检")
        print("      PYTHONIOENCODING=utf-8 python scripts/ocr2md.py --dir guet数据文件 _md")
        print("    ※ 不要用 scripts/prepare_knowledge.py（遗留 MinerU 路线，含硬编码 token，已停用）")


if __name__ == "__main__":
    main()