##################################################
#####  RANK_TOP: notable 중에서도 이번 주 Top N만 별도로 추천  #####
##################################################
"""
curate.py가 판정한 notable 논문 수가 주간 사이클에서도 부담스러운 규모(수십~백여 편)라, 그 중에서
"시간이 없으면 이것만"에 해당하는 상위 N편을 한 번 더 골라 다이제스트 맨 위에 얹는다. notable
집합(이미 소량)만 대상으로 하는 두 번째 판정 콜이라 curate()처럼 청크로 쪼갤 필요는 보통 없다.

이 파일은 도메인(예방의학/역학·직업환경의학·보건행정)에 상관없이 동일하다 — 순위를 매기는 근거는
curate.py가 이미 만들어 둔 필드별 rubric 판정근거(reasoning)이지, 여기서 다시 도메인 지식을 쓰는
게 아니다. 그래서 세 프로젝트 + 공개저장소가 이 파일을 통짜로 공유해도 안전하다(RUBRIC처럼 프로젝트
마다 다른 텍스트가 없음 — 이 파일을 고칠 땐 다른 파일과 달리 cp 동기화가 실제로 맞다).

사용법:
  python rank_top.py                 # 오늘자 assessments 파일로 top N 산출, topn_<date>.json 저장
  python rank_top.py --file <path>   # 특정 raw papers 파일 기준(같은 폴더의 assessments_*도 필요)
"""

import os
import sys
import json

from common import load_config, get_logger, RAW_DIR, today_stamp
import curate

log = get_logger()

TOP_SCHEMA = {
    "type": "object",
    "properties": {
        "top_picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pmid": {"type": "string"},
                    "rank": {"type": "integer"},
                    "why_top": {"type": "string"},
                },
                "required": ["pmid", "rank", "why_top"],
            },
        }
    },
    "required": ["top_picks"],
}


def build_top_prompt(notable_pairs, n):
    lines = [
        f"당신은 저널 다이제스트의 편집자다. 아래는 이번 주 notable로 판정된 논문 {len(notable_pairs)}편과",
        "그 판정근거(다른 심사자가 이미 작성)다. 독자가 시간이 없어 이것만 읽는다면 무엇을 읽어야",
        f"하는지, 상위 {n}편만 골라라.",
        "",
        "고르는 기준: 판정근거에 이미 서술된 참신성·이론적 기여·자료규모·정책파급력이 이 notable",
        "묶음 안에서도 특히 두드러지는 것. 범주가 겹치는 논문끼리는 더 큰 표본, 더 새로운 방법,",
        f"더 직접적인 정책적용을 우선한다. 반드시 정확히 {n}편을 골라 rank 1(가장 중요)부터",
        f"{n}까지 매겨라.",
        "",
    ]
    for paper, assessment in notable_pairs:
        lines.append(f"--- pmid={paper.get('pmid', '')} ---")
        lines.append(f"저널: {paper.get('journal', '')}")
        lines.append(f"제목: {paper.get('title', '')}")
        lines.append(f"범주: {', '.join(assessment.get('categories', [])) or '-'}")
        lines.append(f"판정근거: {assessment.get('reasoning', '')}")
        lines.append(f"요약: {assessment.get('one_line_summary', '')}")
        lines.append("")
    lines.append(
        f"각 항목마다 pmid, rank(1~{n}, 중복 없이), why_top(이 논문이 왜 이번 주 상위권인지 — "
        "다른 상위권 논문과 비교한 1문장, 판정근거를 그대로 반복하지 말 것)을 JSON으로 제출하라."
    )
    return "\n".join(lines)


def pick_top(papers, assessments, cfg):
    """notable=true인 (paper, assessment) 쌍 중 top_n개를 골라 [{pmid, rank, why_top}, ...]로
    반환(rank 오름차순). notable 수가 top_n 이하면 LLM 호출 없이 판정 순서 그대로 전부 반환."""
    cfg_curation = cfg.get("curation", {})
    top_n = cfg_curation.get("top_n", 10)

    by_pmid = {p["pmid"]: p for p in papers}
    notable_pairs = [
        (by_pmid[a["pmid"]], a) for a in assessments
        if a.get("notable") and a["pmid"] in by_pmid
    ]

    if not notable_pairs:
        return []

    if len(notable_pairs) <= top_n:
        log.info(f"[rank_top] notable {len(notable_pairs)}편이 top_n({top_n}) 이하 — "
                  "랭킹 생략, 전부 상위권으로 취급")
        return [
            {"pmid": p["pmid"], "rank": i + 1, "why_top": a.get("one_line_summary", "")}
            for i, (p, a) in enumerate(notable_pairs)
        ]

    backend_name = cfg_curation.get("backend", "claude_cli")
    if backend_name not in curate.BACKENDS:
        raise ValueError(f"알 수 없는 curation.backend={backend_name!r}")

    prompt = build_top_prompt(notable_pairs, top_n)
    payload = curate.BACKENDS[backend_name](
        prompt, cfg_curation, TOP_SCHEMA,
        tool_name="submit_top_picks", tool_description="이번 주 top N 추천 목록을 제출한다.",
    )
    picks = payload.get("top_picks", [])
    picks.sort(key=lambda x: x.get("rank", 999))
    return picks[:top_n]


if __name__ == "__main__":
    cfg = load_config()
    if "--file" in sys.argv:
        papers_path = sys.argv[sys.argv.index("--file") + 1]
        stamp = os.path.basename(papers_path).replace("papers_", "").replace(".json", "")
    else:
        stamp = today_stamp()
        papers_path = os.path.join(RAW_DIR, f"papers_{stamp}.json")
    assessments_path = os.path.join(RAW_DIR, f"assessments_{stamp}.json")

    with open(papers_path, "r", encoding="utf-8") as f:
        papers = json.load(f)
    with open(assessments_path, "r", encoding="utf-8") as f:
        assessments = json.load(f)

    log.info(f"[rank_top] {papers_path} / {assessments_path} 기준 top N 산출 시작")
    top_picks = pick_top(papers, assessments, cfg)
    out_path = os.path.join(RAW_DIR, f"topn_{stamp}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(top_picks, f, ensure_ascii=False, indent=2)
    log.info(f"[rank_top] top {len(top_picks)}편 산출 완료 -> {out_path}")
