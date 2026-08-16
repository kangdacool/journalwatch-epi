##################################################
#####  RUN_DAILY: fetch -> curate -> archive -> notion_sync 오케스트레이션  #####
##################################################
"""
python run_daily.py             # 정상 실행
python run_daily.py --dry-run   # curate까지만(프롬프트 출력), archive/notion 없음, state 갱신 없음
"""

import sys
import json
import os

from common import load_config, get_logger, RAW_DIR, today_stamp
import state as state_mod
import journals as journals_mod
import fetch_pubmed
import curate
import archive
import rank_top
import notion_sync

log = get_logger()


def run(dry_run=False):
    log.info("===== journalwatch-epi 실행 시작 =====")
    cfg = load_config()

    # 0) 저널 유니버스(캐시 최신이면 스킵)
    journals_mod.build_journal_universe(force=False)

    # 1) 수집(토큰 0)
    raw_path = fetch_pubmed.fetch_all(dump_only=False)
    with open(raw_path, "r", encoding="utf-8") as f:
        papers = json.load(f)

    if not papers:
        log.info("[run_daily] 신규 논문 없음 — 종료")
        if not dry_run:
            st = state_mod.load_state()
            st = state_mod.mark_run_success(st)
            state_mod.save_state(st)
        return

    # 2) 판정(토큰 사용, 헤드리스 Claude)
    assessments = curate.curate(papers, cfg, dry_run=dry_run)
    if dry_run:
        log.info("[run_daily] --dry-run: curate 프롬프트만 출력하고 종료")
        return

    stamp = today_stamp()
    with open(os.path.join(RAW_DIR, f"assessments_{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(assessments, f, ensure_ascii=False, indent=2)

    # 3) 아카이브(로컬 정본)
    written = archive.archive_notable(papers, assessments)

    # 3.5) 이번 주 Top N 추천 — notable set(이미 소량)만 대상으로 한 번 더 판정하는 가벼운 콜.
    # 실패해도 다이제스트 본체는 살려야 하므로 여기서 죽지 않고 top_picks=None으로 계속 진행.
    try:
        top_picks = rank_top.pick_top(papers, assessments, cfg)
        with open(os.path.join(RAW_DIR, f"topn_{stamp}.json"), "w", encoding="utf-8") as f:
            json.dump(top_picks, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"[run_daily] Top N 산출 실패(다이제스트는 Top N 없이 계속): {e}")
        top_picks = None

    # 4) Notion push — page_id를 파일로 남긴다. 이 저장소는 단일 파이프라인 템플릿이라 자체적으로는
    # 안 쓰지만, 여러 인스턴스를 나란히 돌리는 사용자가 취합 문서를 만들 때 재사용하기 좋다.
    try:
        page_id = notion_sync.push_digest(written, top_picks=top_picks)
        if page_id:
            with open(os.path.join(RAW_DIR, f"digest_page_id_{stamp}.txt"), "w", encoding="utf-8") as f:
                f.write(page_id)
    except Exception as e:
        log.error(f"[run_daily] Notion push 실패(로컬 아카이브는 이미 완료): {e}")

    # 5) 상태 갱신(성공 시에만 — 실패하면 다음 실행이 같은 구간을 다시 훑어 재시도)
    st = state_mod.load_state()
    st = state_mod.add_seen(st, [p["pmid"] for p in papers])
    st = state_mod.mark_run_success(st)
    state_mod.save_state(st)

    n_notable = sum(1 for a in assessments if a.get("notable"))
    log.info(f"[run_daily] 완료 — 신규 {len(papers)}편 중 notable {n_notable}편")
    log.info("===== 실행 종료 =====")


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
