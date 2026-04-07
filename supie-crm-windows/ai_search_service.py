from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
KNOWLEDGE_FILES = [
    BASE_DIR / "docs" / "manuals" / "USER_MANUAL.md",
    BASE_DIR / "docs" / "manuals" / "USER_MANUAL_PRINT.md",
    BASE_DIR / "README.md",
]
DOMAIN_TERMS = [
    "项目",
    "任务",
    "里程碑",
    "风险",
    "客户",
    "商机",
    "合同",
    "审批",
    "回款",
    "开票",
    "发票",
    "工作台",
    "总览",
    "结项",
    "删除",
    "回收站",
    "跟进",
    "角色",
    "权限",
    "谁能",
    "怎么",
    "在哪",
    "多久",
    "条件",
    "流程",
]


def clip_text(text: str | None, limit: int = 140) -> str:
    raw = " ".join((text or "").strip().split())
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1].rstrip() + "…"


def _normalize_text(text: str | None) -> str:
    return " ".join((text or "").replace("\r", "\n").split())


def extract_query_terms(query: str) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []
    terms: list[str] = []
    lowered = q.lower()
    for term in DOMAIN_TERMS:
        if term in q or term.lower() in lowered:
            terms.append(term)
    for token in re.findall(r"[A-Za-z0-9_/-]{2,}", lowered):
        terms.append(token)
    for token in re.findall(r"[\u4e00-\u9fff]{2,8}", q):
        terms.append(token)
        if len(token) > 4:
            terms.extend(token[i : i + 2] for i in range(len(token) - 1))
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        cleaned = term.strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(term.strip())
    return deduped


def score_text_match(query: str, title: str, body: str) -> int:
    if not query:
        return 0
    combined = f"{title}\n{body}".lower()
    title_l = title.lower()
    query_l = query.lower().strip()
    score = 0
    if query_l and query_l in combined:
        score += 30
    if query_l and query_l in title_l:
        score += 15
    for term in extract_query_terms(query):
        term_l = term.lower()
        if term_l in title_l:
            score += 10
        elif term_l in combined:
            score += 4
    return score


def _iter_markdown_sections(path: Path) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8", errors="ignore")
    lines = content.splitlines()
    sections: list[dict[str, Any]] = []
    current_title = path.stem
    current_lines: list[str] = []

    def flush() -> None:
        body = "\n".join(current_lines).strip()
        if not body:
            return
        sections.append(
            {
                "source_type": "knowledge",
                "source_name": path.name,
                "path": str(path),
                "title": current_title,
                "body": body,
            }
        )

    for line in lines:
        if re.match(r"^#{1,4}\s+", line):
            flush()
            current_title = re.sub(r"^#{1,4}\s+", "", line).strip() or path.stem
            current_lines = []
            continue
        current_lines.append(line)
    flush()
    return sections


@lru_cache(maxsize=1)
def load_knowledge_chunks() -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for path in KNOWLEDGE_FILES:
        if path.exists():
            chunks.extend(_iter_markdown_sections(path))
    docs_dir = BASE_DIR / "docs" / "20260401"
    if docs_dir.exists():
        for path in sorted(docs_dir.glob("*.md")):
            chunks.extend(_iter_markdown_sections(path))
    return chunks


def rank_knowledge_chunks(query: str, limit: int = 5) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for chunk in load_knowledge_chunks():
        score = score_text_match(query, str(chunk.get("title") or ""), str(chunk.get("body") or ""))
        if score <= 0:
            continue
        ranked.append({**chunk, "score": score})
    ranked.sort(key=lambda item: (int(item.get("score") or 0), len(str(item.get("body") or ""))), reverse=True)
    return ranked[:limit]


def rank_semantic_hits(query: str, hits: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for hit in hits:
        title = str(hit.get("title") or "")
        body = str(hit.get("snippet") or hit.get("body") or "")
        score = score_text_match(query, title, body)
        if score <= 0:
            continue
        ranked.append({**hit, "score": score})
    ranked.sort(key=lambda item: (int(item.get("score") or 0), str(item.get("updated_at") or "")), reverse=True)
    return ranked[:limit]


def build_knowledge_answer(query: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    if not chunks:
        return {
            "summary": "当前知识库中没有找到足够相关的制度或流程内容。",
            "sections": [],
            "citations": [],
        }
    top = chunks[0]
    sections = []
    citations = []
    for chunk in chunks[:3]:
        sections.append(
            {
                "title": f"{chunk.get('source_name') or '知识文档'} / {chunk.get('title') or '相关章节'}",
                "body": clip_text(str(chunk.get("body") or ""), 220),
            }
        )
        citations.append(
            {
                "title": f"{chunk.get('source_name') or '知识文档'} · {chunk.get('title') or '相关章节'}",
                "path": chunk.get("path"),
            }
        )
    summary = (
        f"基于当前用户手册与 PRD，最相关的答案来自「{top.get('source_name') or '知识文档'} / "
        f"{top.get('title') or '相关章节'}」。"
    )
    return {"summary": summary, "sections": sections, "citations": citations}


def build_semantic_answer(query: str, hits: list[dict[str, Any]]) -> dict[str, Any]:
    if not hits:
        return {
            "summary": "当前没有找到高相关的业务记录或知识片段。",
            "sections": [],
            "citations": [],
        }
    summary = f"已找到 {len(hits)} 条高相关结果，优先查看前几条业务记录和制度说明。"
    sections = [
        {
            "title": hit.get("kind") or "匹配结果",
            "body": f"{hit.get('title') or '未命名'}：{clip_text(str(hit.get('snippet') or hit.get('body') or ''), 180)}",
        }
        for hit in hits[:5]
    ]
    citations = [
        {"title": hit.get("title") or "匹配结果", "path": hit.get("path"), "link": hit.get("link")}
        for hit in hits[:5]
    ]
    return {"summary": summary, "sections": sections, "citations": citations}
