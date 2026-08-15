##################################################
#####  NOTION_SYNC: 그날의 다이제스트를 Notion 페이지로 push  #####
##################################################
"""
notion-editing 스킬(D:\\onedrive\\claude\\claude-config\\skills\\notion-editing\\SKILL.md)의
markdown API 패턴을 그대로 따른다: POST /v1/pages 에 markdown 파라미터로 페이지를 한 번에 생성.
표(있는 만큼 전부 훑어야 하는 문제)를 보완하려고 상단에 요약표 + 네이티브 목차 블록을 추가로
끼워넣는다 — 목차는 markdown 문법에 없는 블록이라, 자리표시 문단을 심어 만든 뒤(markdown으로
생성) 그 블록을 찾아 뒤에 table_of_contents를 끼우고 자리표시는 지우는 방식이다(notion-editing
스킬의 "그림처럼 markdown으로 못 넣는 요소" 패턴을 그대로 재사용).

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

from common import load_config, get_logger, PROJ_ROOT, DATA_DIR, today_stamp

log = get_logger()
load_dotenv(os.path.join(PROJ_ROOT, ".env"))

NOTION_VERSION = "2026-03-11"  # markdown 파라미터가 이 버전 이상에서만 동작(실측 확인 사항)

_RE_LEADING_NUM = re.compile(r"^(\s*\d+)\.\s")
_TOC_MARKER = "\u27e6TOC\u27e7"  # ⟦TOC⟧ — 임의 문자열이면 됨, 논문 텍스트와 절대 안 겹치게

CATEGORY_LABELS = {
    "A": "방법론적 참신성",
    "B": "역학 이론에 대한 기여",
    "C": "대규모·독보적 자료원",
    "D": "정책적 파급력",
}
CATEGORY_ORDER = ["A", "B", "C", "D", None]  # None = 범주 미기재(이론상 안 나와야 정상)


def bullet_text(s: str) -> str:
    return _RE_LEADING_NUM.sub(lambda m: m.group(1) + "\\. ", s or "")


def _table_cell(s: str, max_len: int = 70) -> str:
    """마크다운 HTML 표 셀에 넣을 텍스트 — 파이프·개행 제거 + 길면 자르기(상세는 아래 본문에 있음)."""
    s = (s or "").replace("\n", " ").replace("|", "/")
    if len(s) > max_len:
        s = s[:max_len - 1].rstrip() + "…"
    return s


def get_client() -> Client:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise RuntimeError(".env 에 NOTION_TOKEN이 없습니다 — notion_sync.py 상단 준비 절차 참조")
    return Client(auth=token, notion_version=NOTION_VERSION)


def _load_journal_order() -> dict:
    """journals.json에 저널이 등장하는 순서(지정 목록 먼저, 그다음 Scimago SJR 내림차순)를
    그대로 표 정렬 기준으로 쓴다 — 매일 같은 순서라 사용자가 익힐 수 있다. 이름 매칭은
    fetch_pubmed.py가 각 논문에 남긴 source_journal_query(그 논문을 찾아낸 질의의 저널명,
    journals.json의 name과 정확히 같은 문자열)로 한다 — PubMed가 돌려주는 journal 필드는
    표기가 제각각(대소문자·약어)이라 그걸로 매칭하면 깨진다."""
    path = os.path.join(DATA_DIR, "journals.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {j["name"]: i for i, j in enumerate(data.get("journals", []))}


def _sort_by_journal(written_pairs, journal_order):
    big = len(journal_order) + 1
    return sorted(
        written_pairs,
        key=lambda pa: (
            journal_order.get(pa[0].get("source_journal_query"), big),
            pa[0].get("journal", ""),
            pa[0].get("title", ""),
        ),
    )


def _group_by_primary_category(written_pairs):
    groups = {k: [] for k in CATEGORY_ORDER}
    for paper, assessment in written_pairs:
        cats = assessment.get("categories") or []
        primary = cats[0] if cats else None
        groups.setdefault(primary, []).append((paper, assessment))
    return groups


def build_digest_markdown(written_pairs: list) -> str:
    n = len(written_pairs)
    # 아래 그룹 헤더와 숫자가 어긋나면 헷갈리니, 요약 tally도 "주(첫번째) 범주" 기준으로 센다
    # (한 논문이 여러 범주에 해당해도 그룹에는 한 번만 들어가는 것과 맞춘다).
    groups_for_tally = _group_by_primary_category(written_pairs)
    tally_str = " · ".join(
        f"{k} {len(groups_for_tally.get(k) or [])}" for k in "ABCD"
    )

    lines = [
        f"# {today_stamp()} 예방의학/역학 주목 논문",
        "",
        f"**notable {n}편** — {tally_str}",
        "",
        _TOC_MARKER,
        "",
    ]

    # ----- 요약표: 저널별로 묶어서(매일 같은 순서라 눈에 익음) 한눈에 스캔 -----
    journal_order = _load_journal_order()
    table_rows = _sort_by_journal(written_pairs, journal_order)
    lines.append("| 저널 | 제목 | 범주 | 한줄요약 |")
    lines.append("| --- | --- | --- | --- |")
    for paper, assessment in table_rows:
        journal = _table_cell(paper.get("journal", ""), 30)
        title = _table_cell(bullet_text(paper.get("title", "")), 60)
        cats = ", ".join(assessment.get("categories", [])) or "-"
        summary = _table_cell(assessment.get("one_line_summary", ""), 70)
        lines.append(f"| {journal} | {title} | {cats} | {summary} |")
    lines.append("")

    # ----- 상세: 범주(왜 골랐는지)별로 묶어서 -----
    groups = _group_by_primary_category(written_pairs)
    for cat in CATEGORY_ORDER:
        items = groups.get(cat) or []
        if not items:
            continue
        label = CATEGORY_LABELS.get(cat, "범주 미기재")
        lines.append(f"# {cat + '. ' if cat else ''}{label} ({len(items)}편)")
        lines.append("")
        for paper, assessment in items:
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


def _insert_toc_block(notion: Client, page_id: str):
    """_TOC_MARKER가 들어간 문단 블록을 찾아 그 뒤에 table_of_contents 블록을 끼우고,
    자리표시 문단은 지운다. markdown 파라미터가 table_of_contents 문법을 지원하지 않아
    (notion-editing 스킬 문서의 "지원되는 문법" 목록에 없음) 2단계로 처리한다."""
    marker_block_id = None
    cursor = None
    for _ in range(10):  # 안전판 — 페이지네이션 10회(1000블록)면 충분
        kwargs = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.blocks.children.list(**kwargs)
        for b in resp.get("results", []):
            if b.get("type") == "paragraph":
                texts = b["paragraph"].get("rich_text", [])
                if texts and texts[0].get("plain_text", "") == _TOC_MARKER:
                    marker_block_id = b["id"]
                    break
        if marker_block_id or not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    if not marker_block_id:
        log.warning("[notion] TOC 자리표시 블록을 못 찾음 — 목차 삽입 생략")
        return

    notion.request(
        path=f"blocks/{page_id}/children", method="patch",
        body={"children": [{"type": "table_of_contents", "table_of_contents": {}}],
              "position": {"type": "after_block", "after_block": {"id": marker_block_id}}},
    )
    notion.request(path=f"blocks/{marker_block_id}", method="delete")


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
    try:
        _insert_toc_block(notion, page_id)
    except Exception as e:
        log.warning(f"[notion] 목차 삽입 실패(다이제스트 본문은 정상 생성됨): {e}")
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
