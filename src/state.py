##################################################
#####  STATE: seen.json (PMID 멱등성 + 마지막 실행일)  #####
##################################################
import os
import json
from datetime import date, timedelta

from common import DATA_DIR

STATE_PATH = os.path.join(DATA_DIR, "seen.json")


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {"seen_pmids": [], "last_run_date": None}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_seen_set(state: dict) -> set:
    return set(state.get("seen_pmids", []))


def add_seen(state: dict, pmids) -> dict:
    seen = get_seen_set(state)
    seen.update(pmids)
    state["seen_pmids"] = sorted(seen)
    return state


def get_fetch_window(state: dict, lookback_days: int):
    """마지막 성공 실행일 - lookback_days ~ 오늘. 최초 실행이면 lookback_days만."""
    last_run = state.get("last_run_date")
    if last_run:
        mindate = date.fromisoformat(last_run) - timedelta(days=lookback_days)
    else:
        mindate = date.today() - timedelta(days=lookback_days)
    return mindate, date.today()


def mark_run_success(state: dict) -> dict:
    state["last_run_date"] = date.today().isoformat()
    return state
