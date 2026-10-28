# fetch_doc.py —— 从学校官网抓「正文在附件里」的公文（修复下载不全的 PDF）
#
# 背景：guet.edu.cn 信息公开网的公文页，**正文其实是页面里的附件 PDF**。
# 用浏览器「打印成 PDF」只会得到网页外壳（标题 + 导航 + 页脚），正文全部丢失——
# 出来的 PDF 只有 2 页、约 370 字，而且没有任何报错，静默地污染语料。
#
# 本脚本从网页 HTML 里定位附件 PDF，下载后走 mdify 转成 markdown。
# 输出文件名沿用「坏掉的 PDF」的文件名，保证和已有语料/测试集的命名一致。
#
# 用法（在项目根目录执行）：
#   # 从坏掉的 PDF 里读出网页地址，批量重抓（推荐）
#   python scripts/fetch_doc.py --from-pdf "guet数据文件/规章制度/本科生管理制度" "_md/规章制度/本科生管理制度"
#
#   # 单个网页
#   python scripts/fetch_doc.py --url https://www.guet.edu.cn/pubinfo/.../page.htm "_md/规章制度/本科生管理制度"
#
#   # 只看会下载什么，不写盘
#   python scripts/fetch_doc.py --from-pdf "guet数据文件/规章制度/本科生管理制度" --check

import argparse
import os
import re
import sys
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from documents import mdify  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_ATTACH_RE = re.compile(r'(?:href|src)=["\']([^"\']*\.(?:pdf|docx?))["\']', re.I)
_NOISE_URL_RE = re.compile(r"https?://[^\s一-鿿]+")


def _clean_stem(stem: str) -> str:
    """文件名归一：NFKC 修康熙部首 + 去空格（文件名字符串会成为知识库里的文档标题）。"""
    import unicodedata
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", stem)) or stem


def http_get(url: str, referer: str | None = None, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        **({"Referer": referer} if referer else {}),
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def urls_from_pdf(path: str) -> list[str]:
    """坏掉的 PDF 里保留着原始网页地址，从里面捞出来。"""
    import fitz
    doc = fitz.open(path)
    try:
        txt = "".join(p.get_text() for p in doc)
    finally:
        doc.close()
    out = []
    for u in sorted(set(_NOISE_URL_RE.findall(txt))):
        u = u.rstrip(".,;")
        if "guet.edu.cn" in u and ".htm" in u:
            out.append(u)
    return out


def text_len(path: str) -> int:
    import fitz
    doc = fitz.open(path)
    try:
        return len("".join(p.get_text() for p in doc).strip())
    finally:
        doc.close()


MIN_CHARS = 500   # 低于此字数视为「下载不全」，才值得重抓；正常的公文不该被重复下载


def attachment_of(page_url: str) -> str | None:
    """打开公文页，找出附件 PDF 的绝对地址。"""
    html = http_get(page_url).decode("utf-8", "ignore")
    m = _ATTACH_RE.search(html)
    return urllib.parse.urljoin(page_url, m.group(1)) if m else None


def main():
    p = argparse.ArgumentParser(description="从官网抓附件 PDF，修复下载不全的公文")
    p.add_argument("dst", nargs="?", default=None, help="输出 md 目录")
    p.add_argument("--url", default=None, help="单个公文页地址")
    p.add_argument("--from-pdf", default=None, help="从该目录里坏掉的 PDF 中读取网页地址")
    p.add_argument("--check", action="store_true", help="只探测，不写盘")
    args = p.parse_args()

    if not args.url and not args.from_pdf:
        p.error("需要 --url 或 --from-pdf")

    # (网页地址, 相对子目录, 输出文件名 stem)
    # ⚠️ 必须带上「相对子目录」：不然 7 个来自不同子目录的公文会全写到 dst 顶层，
    #    既丢了知识库分类，又和原位置的旧文件重复（实测踩过）。
    jobs: list[tuple[str, str, str]] = []
    if args.url:
        jobs.append((args.url, "", os.path.splitext(os.path.basename(args.url))[0] or "doc"))
    if args.from_pdf:
        for dp, _d, ns in os.walk(args.from_pdf):
            rel = os.path.relpath(dp, args.from_pdf)
            rel = "" if rel == "." else rel
            for n in sorted(ns):
                if not n.lower().endswith(".pdf"):
                    continue
                fp = os.path.join(dp, n)
                if text_len(fp) >= MIN_CHARS:
                    continue          # 文字层够多 = 不是「下载不全」，跳过，避免重复抓
                for u in urls_from_pdf(fp):
                    jobs.append((u, rel, os.path.splitext(n)[0]))
                    break

    if not jobs:
        print("没找到可处理的网页地址")
        return

    print(f"待处理 {len(jobs)} 个公文页　→　{'仅探测' if args.check else args.dst}")
    print("-" * 92)

    ok = 0
    for page_url, rel, stem in jobs:
        try:
            att = attachment_of(page_url)
        except Exception as e:
            print(f"❌ 打开网页失败 {page_url[:60]} → {type(e).__name__}: {e}")
            continue
        if not att:
            print(f"⚠️  页面上找不到附件，正文可能是图片/动态加载：{page_url[:66]}")
            continue
        try:
            blob = http_get(att, referer=page_url)
        except Exception as e:
            print(f"❌ 下载附件失败 {att[-50:]} → {type(e).__name__}: {e}")
            continue

        name = os.path.basename(att)
        try:
            md = mdify.to_markdown(name, blob)
        except Exception as e:
            print(f"❌ 转换失败 {name} → {type(e).__name__}: {e}")
            continue

        print(f"✅ {len(md):7,} 字　{stem or name}")
        if args.dst and stem and len(md.strip()) >= 200:
            out = os.path.join(args.dst, rel, _clean_stem(stem) + ".md")
            # 护栏：别用差数据覆盖好数据
            if os.path.exists(out):
                old_len = len(open(out, encoding="utf-8", errors="ignore").read().strip())
                if old_len and len(md.strip()) < old_len * 0.6:
                    print(f"   ⏭️  跳过写入：新结果 {len(md)} 字 远短于现有 {old_len} 字")
                    continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"   💾 {out}")
            ok += 1

    if ok:
        print(f"\n已写入 {ok} 个 md → {args.dst}")


if __name__ == "__main__":
    main()