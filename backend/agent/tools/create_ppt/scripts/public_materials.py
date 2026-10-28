"""Bounded URL reading for an identified source gap; no unrestricted browsing."""
from datetime import datetime, timezone
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import unquote, urljoin, urlsplit

from agent.core.request_control import check_cancelled, remaining_seconds
from .materials import Material


def public_address(url: str):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("只支持不带认证信息的公开HTTP(S)资料链接")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in {80, 443}:
        raise ValueError("公开资料仅允许标准HTTP(S)端口")
    addresses = {row[4][0] for row in socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError("资料链接不能指向本机、内网或保留地址")
    return parts, port, sorted(addresses)[0]


def _fetch(url):
    check_cancelled()
    parts, port, address = public_address(url)
    host = parts.hostname
    if parts.scheme == "https":
        class Pinned(http.client.HTTPSConnection):
            def connect(self):
                raw = socket.create_connection((address, port), timeout=self.timeout)
                self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    else:
        class Pinned(http.client.HTTPConnection):
            def connect(self):
                self.sock = socket.create_connection((address, port), timeout=self.timeout)
    connection = Pinned(host, port, timeout=remaining_seconds(15))
    try:
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        connection.request("GET", path, headers={"User-Agent": "GUET-Presentation/1", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError("公开资料超过2MiB，请提供选定章节")
        return response.status, dict(response.getheaders()), body
    finally:
        connection.close()


def _published_date(soup):
    for attributes in ({'property': 'article:published_time'}, {'itemprop': 'datePublished'},
                       {'name': 'citation_publication_date'}, {'name': 'DC.date.issued'}):
        tag = soup.find('meta', attrs=attributes)
        if tag and tag.get('content'):
            return str(tag['content']).strip() or None
    tag = soup.find('time', attrs={'itemprop': 'datePublished'})
    return str(tag.get('datetime')).strip() if tag and tag.get('datetime') else None


def read_web_document(url, question, *, school_policy=False):
    """Return material plus actual access metadata; HTML is not verified paper full text."""
    if not question.strip():
        raise ValueError("请说明需要补充的资料缺口")
    for _ in range(4):
        check_cancelled()
        parts, _, _ = public_address(url)
        if school_policy and not (parts.hostname == "guet.edu.cn" or parts.hostname.endswith(".guet.edu.cn")):
            raise ValueError("本校具体政策只采用桂林电子科技大学官方来源")
        status, headers, content = _fetch(url)
        headers = {k.lower(): v for k, v in headers.items()}
        if status in {301, 302, 303, 307, 308}:
            if not headers.get("location"):
                raise ValueError("资料跳转缺少地址")
            url = urljoin(url, headers["location"])
            continue
        if status != 200:
            raise ValueError("公开资料读取失败：HTTP %d" % status)
        accessed_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        metadata = {'final_url': url, 'accessed_at': accessed_at, 'published_at': None,
                    'reading_status': 'read', 'untrusted_content': True,
                    'source_host': parts.hostname, 'full_paper_verified': False}
        if "pdf" in headers.get("content-type", "") or content.startswith(b'%PDF-'):
            from .materials import read_materials
            material = read_materials("公开资料.pdf", content)[0]
            import fitz
            with fitz.open(stream=content, filetype='pdf') as document:
                title = (document.metadata.get('title') or '').strip()
            title = title or unquote(parts.path.rsplit('/', 1)[-1]) or '公开PDF'
            metadata.update(content_scope='public_pdf_text', title=title[:300])
            return material.model_copy(update={"source_type": "public", "url": url,
                'title': title[:300],
                "locator": question + "；" + material.locator,
                "date": None}), metadata
        content_type = headers.get('content-type', '').split(';')[0].strip().lower()
        if content_type and content_type not in {'text/html', 'application/xhtml+xml', 'text/plain'}:
            raise ValueError('当前只读取公开网页、纯文本或PDF，不下载其他文件类型')
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(content, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else parts.hostname
        published_at = _published_date(soup)
        citation = soup.find('meta', attrs={'name': 'citation_title'})
        abstract = soup.find('meta', attrs={'name': 'citation_abstract'})
        for element in soup(["script", "style", "nav", "footer", "noscript"]):
            element.decompose()
        main = soup.find("article") or soup.find("main") or soup.body or soup
        text = main.get_text("\n", strip=True)
        if not text:
            raise ValueError("公开资料没有可读取正文")
        scope = 'publication_page' if citation else 'webpage_text'
        if abstract and abstract.get('content') and text.strip() == str(abstract['content']).strip():
            scope = 'abstract'
        metadata.update(title=title[:300], published_at=published_at, content_scope=scope)
        return Material(title=title[:300], text=text, source_type="public", url=url, date=published_at,
                        locator=question, scope="仅支持实际读到的页面内容；论文页面不等于论文全文"), metadata
    raise ValueError("资料跳转超过3次")


def read_public_material(url, question, *, school_policy=False):
    """Compatibility facade for existing explicitly supplied public links."""
    material, _ = read_web_document(url, question, school_policy=school_policy)
    return material
