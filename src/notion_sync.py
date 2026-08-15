##################################################
#####  NOTION_SYNC: 그날의 다이제스트를 Notion 페이지로 push  #####
##################################################
"""
notion-editing 스킬(D:\\onedrive\\claude\\claude-config\\skills\\notion-editing\\SKILL.md)의
markdown API 패턴을 그대로 따른다: POST /v1/pages 에 markdown 파라미터로 페이지를 한 번에 생성.

사전 준비(최초 1회, 사용자가 직접):
  1) notion.so/my-integrations 에서 Internal Integration 생성, 토큰을 .env 에
     NOTION_TOKEN=ntn_... 로 저장
  2) 다이제스트를 넣을 부모 페이지를 만들고 "···" -> 연결 -> 이 integration과 공유
  3) 그 페이지의 id를 config.yaml 의 notion.parent_page_id 에 기록
     (페이지 URL 끝의 32자리 hex, 하이픈 없어도/있어도 무방)

주의: 불릿 텍스트가 "1. "처럼 숫자로 시작하면 Notion 마크다운 파서가 중첩 순서목록으로
오인한다 — bullet_text()로 이스케이프한다(실측 확인 사항, notion-editing 스킬 문서).
"""

import os
import re
import json

from dotenv import load_dotenv
from notion_client import Client

from common import load_config, get_logger, PROJ_ROOT, today_stamp

log = get_logger()
load_dotenv(os.path.join(PROJ_ROOT, ".env"))

NOTION_VERSION = "2026-03-11"  # markdown 파라미터가 이 버전 이상에서만 동작(실측 확인 사항)

_RE_LEADING_NUM = re.compile(r"^(\s*\d+)\.\s")


def bullet_text(s: str) -> str:
    return _RE_LEADING_NUM.sub(lambda m: m.group(1) + "\\. ", s or "")


def get_client() -> Client:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise RuntimeError(".env 에 NOTION_TOKEN이 없습니다 — notion_sync.py 상단 준비 절차 참조")
    return Client(auth=token, notion_version=NOTION_VERSION)


def build_digest_markdown(written_pairs: list) -> str:
    lines = [f"# {today_stamp()} 예방의학/역학 주목 논문", ""]
    for paper, assessment in written_pairs:
        cats = ", ".join(assessment.get("categories", []))
        tags = ", ".join(assessment.get("interest_tags", []))
        title = bullet_text(paper.get("title", "(제목 없음)"))
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"**{paper.get('journal', '')}** · 범주 {cats or '-'}"
                      + (f" · 관심태그 {tags}" if tags else ""))
        lines.append("")
        lines.append(assessment.get("reasoning", ""))
        lines.append("")
        lines.append(assessment.get("one_line_summary", ""))
        lines.append("")
        pmid = paper.get("pmid", "")
        lines.append(f"[PubMed](https://pubmed.ncbi.nlm.nih.gov/{pmid}/)"
                      + (f" · [DOI](https://doi.org/{paper['doi']})" if paper.get("doi") else ""))
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def push_digest(written_pairs: list):
    if not written_pairs:
        log.info("[notion] notable 0건 — 페이지 생성 생략")
        return None

    cfg = load_config()
    parent_id = cfg.get("notion", {}).get("parent_page_id")
    if not parent_id:
        log.warning("[notion] config.yaml에 notion.parent_page_id 없음 — push 생략")
        return None

    notion = get_client()
    md = build_digest_markdown(written_pairs)
    resp = notion.request(
        path="pages", method="post",
        body={
            "parent": {"page_id": parent_id},
            "properties": {"title": {"title": [{"text": {"content": f"{today_stamp()} 저널 다이제스트"}}]}},
            "markdown": md,
        },
    )
    page_id = resp.get("id")
    log.info(f"[notion] 다이제스트 페이지 생성 완료: {page_id}")
    return page_id


if __name__ == "__main__":
    import sys
    from common import RAW_DIR

    stamp = sys.argv[1] if len(sys.argv) > 1 else today_stamp()
    with open(os.path.join(RAW_DIR, f"papers_{stamp}.json"), "r", encoding="utf-8") as f:
        papers = json.load(f)
    with open(os.path.join(RAW_DIR, f"assessments_{stamp}.json"), "r", encoding="utf-8") as f:
        assessments = json.load(f)
    by_pmid = {p["pmid"]: p for p in papers}
    pairs = [(by_pmid[a["pmid"]], a) for a in assessments if a.get("notable") and a["pmid"] in by_pmid]
    push_digest(pairs)
