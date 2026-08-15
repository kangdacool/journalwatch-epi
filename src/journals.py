##################################################
#####  JOURNALS: 저널 유니버스 결정 (지정 목록 + Scimago Epidemiology Q1)  #####
##################################################
"""
data/journals.json 을 만든다 — PubMed 검색에 쓸 저널 목록(ISSN 포함).

두 원천을 union(둘 다 config.yaml의 journals: 절에서 설정):
  1) 사용자가 지정한 저널(journals.named) — 쿼타일과 무관하게 무조건 포함
  2) Scimago 카테고리(journals.scimago_category_id, 기본 2713=Epidemiology) 중
     journals.scimago_quartiles(기본 ["Q1"])에 해당하는 저널 — 안전망

**다른 분야로 커스터마이즈하려면:** scimagojr.com/journalrank.php 에서 원하는 카테고리를 고르면
URL에 `category=NNNN`이 붙는다 — 그 숫자를 config.yaml의 scimago_category_id에 넣으면 된다.
코드는 건드릴 필요 없다.

Scimago는 Cloudflare가 일반 HTTP 클라이언트(requests/curl)를 403으로 막는다(실측 확인,
2026-08-15) — 반드시 실제 Chrome을 CDP로 띄워서 접근한다(에스컬레이션 사다리: WebFetch →
브라우저 User-Agent → Crossref/OpenAlex API → CDP 실제 Chrome).

사용법:
  python journals.py            # 캐시가 refresh_interval_days보다 오래됐으면 재수집
  python journals.py --force    # 강제 재수집
  python journals.py --dump     # 재수집 없이 현재 캐시 요약 출력

⚠ 브라우저가 필요해 CI(GitHub Actions 등)에서 매일 돌리기엔 무겁다 — 쿼타일은 자주 안 바뀌므로
로컬에서 가끔(기본 90일) 돌려서 data/journals.json을 만들고, 일일 자동실행(fetch_pubmed.py 이하)은
그 캐시 파일만 읽는다.
"""

import os
import sys
import json
import time
import re
import subprocess
from datetime import datetime, timedelta

from common import load_config, get_logger, DATA_DIR

log = get_logger()

JOURNALS_JSON = os.path.join(DATA_DIR, "journals.json")

# config.yaml에 journals.named / scimago_category_id가 없을 때만 쓰는 기본값(이 랩의 실제 설정).
# 다른 분야로 쓰려면 코드가 아니라 config.yaml을 고칠 것 — 이건 그냥 "예시가 비었을 때의 폴백"이다.
DEFAULT_NAMED_JOURNALS = [
    {"name": "American Journal of Epidemiology", "country": None},
    {"name": "International Journal of Epidemiology", "country": None},
    {"name": "Epidemiology", "country": "United States"},
    {"name": "Epidemiology and Health", "country": "South Korea"},
    {"name": "Lancet Public Health", "country": None},
    {"name": "Journal of Preventive Medicine and Public Health", "country": "South Korea"},
    {"name": "European Journal of Epidemiology", "country": None},
    {"name": "Annals of Epidemiology", "country": None},
    {"name": "American Journal of Public Health", "country": None},
    {"name": "PLOS Medicine", "country": None},
]
DEFAULT_SCIMAGO_CATEGORY_ID = 2713  # Epidemiology, scimagojr.com에서 실측 확인 2026-08-15
DEFAULT_SCIMAGO_QUARTILES = ["Q1"]

CDP_PORT = 9223
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def find_chrome():
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    raise FileNotFoundError("chrome.exe를 찾지 못했습니다 — CHROME_CANDIDATES를 확인하세요.")


def launch_cdp_chrome():
    import requests
    import tempfile
    # OneDrive 동기화 대상(data/)에 두면 Chrome 프로필 캐시(수백 MB)가 그대로 클라우드에
    # 올라간다 — 프로젝트 밖 임시 디렉터리를 쓴다(실측: 1회 실행에 339MB, 2026-08-15 확인 후 수정).
    scratch = os.path.join(tempfile.gettempdir(), "journalwatch_epi_chrome_cdp")
    os.makedirs(scratch, exist_ok=True)
    chrome = find_chrome()
    proc = subprocess.Popen(
        [chrome, f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={scratch}",
         "--no-first-run", "--no-default-browser-check",
         "--disable-blink-features=AutomationControlled", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(20):
        try:
            requests.get(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=1)
            return proc
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("CDP Chrome이 기동하지 않았습니다.")


def goto_robust(ctx, url, tries=3, settle_ms=2500):
    """Cloudflare 챌린지/광고 리다이렉트(2mdn.net 등)로 페이지가 튕기는 경우가 있어
    매번 새 탭을 열고, scimagojr.com에 안착했는지 확인 후 아니면 재시도한다(실측 확인 필요했던
    사항 — 새로 만든 프로필에서 카테고리 페이지로 바로 진입하면 0건이 나오는 문제가 있었음)."""
    last_url = None
    for attempt in range(1, tries + 1):
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            page.close()
            continue
        page.wait_for_timeout(settle_ms)
        last_url = page.url
        if "scimagojr.com" in last_url:
            return page
        page.close()
        time.sleep(1)
    raise RuntimeError(f"scimagojr.com 안착 실패({tries}회 시도), 마지막 URL={last_url}")


def parse_issn_from_detail(body_text: str):
    """Scimago 저널 상세페이지 본문에서 'ISSN\\n10575987, 15458601' 패턴을 NNNN-NNNN 리스트로."""
    m = re.search(r"ISSN\s*\n?\s*([0-9xX, \n]+?)(?:\n|Coverage)", body_text)
    if not m:
        return []
    raw = m.group(1)
    codes = re.findall(r"[0-9]{7}[0-9xX]", raw)
    return [f"{c[:4]}-{c[4:]}" for c in codes]


def get_detail_issn(ctx, scimago_id: str):
    page = goto_robust(ctx, f"https://www.scimagojr.com/journalsearch.php?q={scimago_id}&tip=sid&clean=0")
    try:
        return parse_issn_from_detail(page.inner_text("body"))
    finally:
        page.close()


def fetch_scimago_category(ctx, category_id: int, quartiles: list):
    """category 페이지를 SJR 내림차순으로 넘기며 quartiles에 해당하는 동안만 수집
    (SJR 내림차순 정렬이라 원하는 쿼타일들이 최상위에 연속 배치됨 — Q1만 원하면 Q1 구간에서,
    Q1+Q2를 원하면 그 두 구간이 끝나는 지점에서 멈춘다)."""
    quartiles = set(quartiles)
    results = []
    for pnum in range(1, 15):  # 안전판 상한
        url = f"https://www.scimagojr.com/journalrank.php?category={category_id}&page={pnum}"
        page = goto_robust(ctx, url)
        rows = page.eval_on_selector_all(
            "table tr",
            "els => els.map(e => e.innerText)"
        )
        title_links = page.eval_on_selector_all(
            "td a[href*='journalsearch']",
            "els => els.map(e => ({href: e.getAttribute('href'), text: e.textContent.trim()}))"
        )
        page.close()
        if not title_links:
            break
        hit_out_of_range = False
        for i, link in enumerate(title_links):
            row_text = rows[i + 1] if i + 1 < len(rows) else ""
            qm = re.search(r"\bQ[1-4]\b", row_text)
            quartile = qm.group(0) if qm else None
            if quartile not in quartiles:
                hit_out_of_range = True
                break
            idm = re.search(r"q=(\d+)", link["href"])
            if not idm:
                continue
            results.append({"name": link["text"], "scimago_id": idm.group(1), "quartile": quartile})
        if hit_out_of_range or len(title_links) < 20:
            break
    return results


def search_journal(ctx, name: str, country_hint: str = None):
    import urllib.parse
    url = f"https://www.scimagojr.com/journalsearch.php?q={urllib.parse.quote(name)}"
    page = goto_robust(ctx, url)
    links = page.eval_on_selector_all(
        "a[href*='journalsearch.php?q=']",
        "els => els.map(e => ({href: e.getAttribute('href'), text: e.textContent.trim()}))"
                                        ".filter(x => /tip=sid/.test(x.href))"
    )
    page.close()
    candidates = []
    for l in links:
        idm = re.search(r"q=(\d+)&tip=sid", l["href"])
        if idm:
            candidates.append({"id": idm.group(1), "text": l["text"]})
    if not candidates:
        return None

    # 검색결과 텍스트는 "제목+국가+출판사"가 구분자 없이 붙어있다(예: "EpidemiologyUnited
    # StatesLippincott..."). name이 부분문자열로만 들어있는 다른 저널(예: "Journal of Clinical
    # Epidemiology")과 헷갈리지 않으려면 텍스트가 정확히 name으로 "시작"해야 한다 — 실측 확인된
    # 사고: country_hint 부분일치만으로 고르면 "Epidemiology" 검색이 "Journal of Clinical
    # Epidemiology"(둘 다 United States를 포함)로 잘못 매칭됨(2026-08-15).
    def starts_exactly(text):
        # 국가명은 공백 없이 바로 붙고 항상 대문자로 시작한다("EpidemiologyUnited States...").
        # 반대로 더 긴 제목의 연속(예: "Epidemiology and Infection...")은 공백+소문자로 이어진다
        # ("Epidemiology" + " and..."). 그래서 경계문자가 "대문자"여야 country 접합이고, 아니면
        # (공백 등) 다른 제목의 부분일치다 — 실측으로 두 방향 다 뒤집힌 채 확인됨(2026-08-15).
        n = len(name)
        return text[:n].lower() == name.lower() and (len(text) == n or text[n].isupper())

    exact = [c for c in candidates if starts_exactly(c["text"])]
    if country_hint:
        for c in exact:
            if c["text"][len(name):].lower().startswith(country_hint.lower()):
                return c["id"]
    if exact:
        return exact[0]["id"]
    return candidates[0]["id"]


def build_journal_universe(force=False):
    cfg = load_config()
    cfg_journals = cfg.get("journals", {})
    refresh_days = cfg_journals.get("refresh_interval_days", 90)
    named_specs = cfg_journals.get("named", DEFAULT_NAMED_JOURNALS)
    category_id = cfg_journals.get("scimago_category_id", DEFAULT_SCIMAGO_CATEGORY_ID)
    quartiles = cfg_journals.get("scimago_quartiles", DEFAULT_SCIMAGO_QUARTILES)

    if not force and os.path.exists(JOURNALS_JSON):
        with open(JOURNALS_JSON, "r", encoding="utf-8") as f:
            cached = json.load(f)
        resolved_at = datetime.fromisoformat(cached["resolved_at"])
        if datetime.now() - resolved_at < timedelta(days=refresh_days):
            log.info(f"[journals] 캐시 최신(<{refresh_days}일) — 재수집 생략: {JOURNALS_JSON}")
            return cached

    from playwright.sync_api import sync_playwright

    log.info("[journals] Chrome(CDP) 기동 중...")
    proc = launch_cdp_chrome()
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            ctx = browser.contexts[0]

            log.info(f"[journals] Scimago category={category_id} {quartiles} 수집 중...")
            scimago_hits = fetch_scimago_category(ctx, category_id, quartiles)
            log.info(f"[journals] {len(scimago_hits)}개 발견, ISSN 조회 중...")
            for j in scimago_hits:
                j["issn"] = get_detail_issn(ctx, j["scimago_id"])
                j["source"] = "scimago"

            log.info(f"[journals] 지정 {len(named_specs)}개 저널 ISSN 조회 중...")
            named = []
            for spec in named_specs:
                sid = search_journal(ctx, spec["name"], spec.get("country"))
                if not sid:
                    log.warning(f"[journals] 검색 실패(수동 확인 필요): {spec['name']}")
                    named.append({"name": spec["name"], "scimago_id": None, "issn": [],
                                  "quartile": None, "source": "named"})
                    continue
                issn = get_detail_issn(ctx, sid)
                named.append({"name": spec["name"], "scimago_id": sid, "issn": issn,
                               "quartile": None, "source": "named"})

            browser.close()
    finally:
        proc.terminate()

    # union: named 목록의 ISSN을 우선 신뢰, scimago 히트 중 named와 ISSN 겹치는 항목은 named로 흡수
    named_issns = set(i for j in named for i in j["issn"])
    scimago_dedup = [j for j in scimago_hits if not (set(j["issn"]) & named_issns)]

    result = {
        "resolved_at": datetime.now().isoformat(),
        "category": f"Scimago category id={category_id}, quartiles={quartiles}",
        "journals": named + scimago_dedup,
    }
    with open(JOURNALS_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info(f"[journals] 저장 완료: {len(result['journals'])}개 저널 -> {JOURNALS_JSON}")

    missing_issn = [j["name"] for j in result["journals"] if not j["issn"]]
    if missing_issn:
        log.warning(f"[journals] ISSN 조회 실패({len(missing_issn)}개, 수동 확인 필요): {missing_issn}")

    return result


if __name__ == "__main__":
    if "--dump" in sys.argv:
        if not os.path.exists(JOURNALS_JSON):
            print("journals.json 없음 — 먼저 python journals.py 실행")
            sys.exit(1)
        with open(JOURNALS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"resolved_at={data['resolved_at']}  n={len(data['journals'])}")
        for j in data["journals"]:
            print(f"  [{j['source']:11s}] {j['name']:60s} ISSN={j['issn']}  Q={j.get('quartile')}")
    else:
        build_journal_universe(force="--force" in sys.argv)
