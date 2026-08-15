##################################################
#####  ARCHIVE: notable 논문 -> 로컬 마크다운 정본(Obsidian-ready)  #####
##################################################
"""
curate.py의 assessments + fetch_pubmed.py의 raw 메타데이터를 합쳐 notable=true인 논문마다
archive/papers/<date>_<pmid>.md 를 만든다. 이 폴더가 그대로 Obsidian vault가 될 수 있도록
frontmatter를 신중히 설계한다.
"""

import os
import re
import json

from common import PAPERS_DIR, today_stamp, get_logger

log = get_logger()

_SAFE_RE = re.compile(r"[^\w\s-]", re.UNICODE)


def _yaml_escape(s: str) -> str:
    return (s or "").replace('"', '\\"').replace("\n", " ").strip()


def _yaml_list(items):
    if not items:
        return "[]"
    return "[" + ", ".join(f'"{_yaml_escape(i)}"' for i in items) + "]"


def build_markdown(paper: dict, assessment: dict) -> str:
    categories = assessment.get("categories", [])
    tags = assessment.get("interest_tags", [])
    fm = [
        "---",
        f'title: "{_yaml_escape(paper.get("title", ""))}"',
        f'journal: "{_yaml_escape(paper.get("journal", ""))}"',
        f'pmid: "{paper.get("pmid", "")}"',
        f'doi: "{_yaml_escape(paper.get("doi") or "")}"',
        f'pubdate: "{_yaml_escape(paper.get("pubdate", ""))}"',
        f"categories: {_yaml_list(categories)}",
        f"interest_tags: {_yaml_list(tags)}",
        f'archived: "{today_stamp()}"',
        "---",
        "",
        f"# {paper.get('title', '(제목 없음)')}",
        "",
        f"- **저널**: {paper.get('journal', '')}  ",
        f"- **저자**: {', '.join(paper.get('authors', []))}  ",
        f"- **PMID**: [{paper.get('pmid', '')}](https://pubmed.ncbi.nlm.nih.gov/{paper.get('pmid', '')}/)"
        + (f"  · **DOI**: [{paper['doi']}](https://doi.org/{paper['doi']})" if paper.get("doi") else ""),
        f"- **판정 범주**: {', '.join(categories) if categories else '(없음)'}",
        "",
        "## 왜 주목할 만한가",
        "",
        assessment.get("reasoning", ""),
        "",
        "## 요약",
        "",
        assessment.get("one_line_summary", ""),
        "",
        "## 초록 (원문)",
        "",
        paper.get("abstract", "(초록 없음)"),
        "",
    ]
    return "\n".join(fm)


def archive_notable(papers: list, assessments: list) -> list:
    """notable=true인 논문들을 마크다운 파일로 저장. 저장된 (paper, assessment) 쌍 리스트를 반환."""
    by_pmid = {p["pmid"]: p for p in papers}
    stamp = today_stamp()
    written = []
    for a in assessments:
        if not a.get("notable"):
            continue
        paper = by_pmid.get(a["pmid"])
        if not paper:
            log.warning(f"[archive] assessment의 pmid={a['pmid']}에 해당하는 raw 논문 없음 — 스킵")
            continue
        md = build_markdown(paper, a)
        out_path = os.path.join(PAPERS_DIR, f"{stamp}_{a['pmid']}.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
        written.append((paper, a))
        log.info(f"[archive] 저장: {out_path}")
    log.info(f"[archive] notable {len(written)}편 아카이브 완료 (전체 {len(assessments)}편 중)")
    return written


if __name__ == "__main__":
    import sys
    from common import RAW_DIR

    stamp = sys.argv[1] if len(sys.argv) > 1 else today_stamp()
    with open(os.path.join(RAW_DIR, f"papers_{stamp}.json"), "r", encoding="utf-8") as f:
        papers = json.load(f)
    with open(os.path.join(RAW_DIR, f"assessments_{stamp}.json"), "r", encoding="utf-8") as f:
        assessments = json.load(f)
    archive_notable(papers, assessments)
