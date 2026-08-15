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

    # 4) Notion push
    try:
        notion_sync.push_digest(written)
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
