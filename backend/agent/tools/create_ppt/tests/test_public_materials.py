import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from agent.tools.create_ppt.scripts.content import public_materials as public


@pytest.mark.parametrize("url,address", [
    ("http://localhost/x", "127.0.0.1"),
    ("https://example.com/x", "10.0.0.1"),
    ("http://169.254.169.254/", "169.254.169.254"),
])
def test_private_targets_blocked(monkeypatch, url, address):
    monkeypatch.setattr(public.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (address, 80))])
    with pytest.raises(ValueError):
        public.public_address(url)


def test_official_source_boundary_and_redirect_rechecked(monkeypatch):
    monkeypatch.setattr(public.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))])
    with pytest.raises(ValueError):
        public.read_public_material("https://other-school.edu.cn/policy", "学校政策", school_policy=True)
    monkeypatch.setattr(public, "_fetch", lambda *a: (302, {"Location": "http://127.0.0.1/private"}, b""))
    def resolver(host, *a, **k):
        return [(2, 1, 6, "", ("127.0.0.1" if host == "127.0.0.1" else "8.8.8.8", 443))]
    monkeypatch.setattr(public.socket, "getaddrinfo", resolver)
    with pytest.raises(ValueError):
        public.read_public_material("https://example.org/source", "背景")


def test_public_source_keeps_url_date_and_question(monkeypatch):
    monkeypatch.setattr(public.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))])
    html = '<html><head><title>公开统计</title><meta property="article:published_time" content="2025-01-01"></head><body><article>样本为2024年在校学生。</article></body></html>'
    monkeypatch.setattr(public, "_fetch", lambda *a: (200, {"Content-Type": "text/html"}, html.encode()))
    material = public.read_public_material("https://example.org/source", "补充调查对象")
    assert material.url == "https://example.org/source" and material.date == "2025-01-01"
    assert material.locator == "补充调查对象" and "2024" in material.text


def test_gap_research_reads_only_user_candidate_and_preserves_source(monkeypatch):
    from agent.tools.create_ppt.scripts.execution import subgraph
    from agent.tools.create_ppt.scripts.content.materials import Material
    seen = []
    def read(url, question, **kwargs):
        seen.append((url, question))
        return Material(title="公开背景", text="全国2024年情况", source_type="public", url=url, date="2025-06-11")
    monkeypatch.setattr(public, "read_public_material", read)
    question = "全国背景"
    task = {"question": question, "name": "read_public_material", "args": {"url": "https://example.org/data", "question": question}}
    state = {"brief": {"material_policy": "public", "public_candidates": [{"url": "https://example.org/data"}]},
             "plan": {"research_tasks": [task]}, "open_questions": [question],
             "budget": {"tool_calls_left": 2, "research_steps_left": 2}}
    result = subgraph._research(state)
    assert seen == [("https://example.org/data", question)]
    evidence = next(iter(result["evidence"].values()))
    assert evidence["url"] == "https://example.org/data" and evidence["date"] == "2025-06-11"
    state["brief"]["public_candidates"] = []
    with pytest.raises(ValueError):
        subgraph._research(state)
    assert len(seen) == 1
