##################################################
#####  FETCH_PUBMED: NCBI E-utilities로 신규 논문 raw 수집 (토큰 0)  #####
##################################################
"""
저널별 esearch(ISSN + edat 범위) -> PMID 목록 -> efetch(배치) -> raw 메타데이터.
NCBI E-utilities만 쓴다(agent/feedback/key_reference_reading.md) — 저널 웹사이트는 건드리지 않음.

사용법:
  python fetch_pubmed.py            # 정상 수집, data/raw/papers_<date>.json 저장
  python fetch_pubmed.py --dump     # 저널별 esearch 히트 수만 출력(발행빈도 대조용, 파일 저장 안 함)
"""

import os
import sys
import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from common import load_config, get_logger, RAW_DIR, DATA_DIR, today_stamp
import state as state_mod

log = get_logger()

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
JOURNALS_JSON = os.path.join(DATA_DIR, "journals.json")


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "journalwatch-epi/1.0 (research tool)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def load_journals():
    if not os.path.exists(JOURNALS_JSON):
        raise FileNotFoundError("data/journals.json 없음 — 먼저 python journals.py 실행")
    with open(JOURNALS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [j for j in data["journals"] if j.get("issn")]


def esearch_journal(issn_list, mindate, maxdate, cfg_eutils, retmax=200):
    """ISSN 중 아무거나(전자/인쇄) 매치되면 OR로 묶어 검색."""
    issn_term = " OR ".join(f'"{i}"[issn]' for i in issn_list)
    term = f"({issn_term}) AND ({mindate:%Y/%m/%d}:{maxdate:%Y/%m/%d}[edat])"
    params = {
        "db": "pubmed", "term": term, "retmode": "json", "retmax": retmax,
        "datetype": "edat",
    }
    if cfg_eutils.get("email"):
        params["email"] = cfg_eutils["email"]
    if cfg_eutils.get("api_key"):
        params["api_key"] = cfg_eutils["api_key"]
    url = f"{EUTILS}/esearch.fcgi?{urllib.parse.urlencode(params)}"
    body = json.loads(_get(url))
    return body.get("esearchresult", {}).get("idlist", [])


def _text(elem, path, default=None):
    node = elem.find(path)
    return node.text if node is not None and node.text else default


def parse_efetch_xml(xml_bytes):
    root = ET.fromstring(xml_bytes)
    papers = []
    for art in root.findall(".//PubmedArticle"):
        pmid = _text(art, ".//MedlineCitation/PMID")
        if not pmid:
            continue
        title = _text(art, ".//Article/ArticleTitle", "")
        abstract_parts = [
            (n.text or "") for n in art.findall(".//Article/Abstract/AbstractText")
        ]
        abstract = " ".join(abstract_parts).strip()
        journal = _text(art, ".//Article/Journal/Title") \
            or _text(art, ".//Article/Journal/ISOAbbreviation", "")
        doi = None
        for eloc in art.findall(".//ELocationID"):
            if eloc.get("EIdType") == "doi":
                doi = eloc.text
        year = _text(art, ".//Article/Journal/JournalIssue/PubDate/Year")
        month = _text(art, ".//Article/Journal/JournalIssue/PubDate/Month", "")
        medline_date = _text(art, ".//Article/Journal/JournalIssue/PubDate/MedlineDate")
        pubdate = f"{year} {month}".strip() if year else (medline_date or "")
        authors = []
        for au in art.findall(".//Article/AuthorList/Author")[:5]:
            last = _text(au, "LastName")
            fore = _text(au, "ForeName")
            if last:
                authors.append(f"{last} {fore}" if fore else last)
        papers.append({
            "pmid": pmid, "title": title, "abstract": abstract, "journal": journal,
            "doi": doi, "pubdate": pubdate, "authors": authors,
        })
    return papers


def efetch_batch(pmids, cfg_eutils):
    params = {"db": "pubmed", "id": ",".join(pmids), "rettype": "abstract", "retmode": "xml"}
    if cfg_eutils.get("email"):
        params["email"] = cfg_eutils["email"]
    if cfg_eutils.get("api_key"):
        params["api_key"] = cfg_eutils["api_key"]
    url = f"{EUTILS}/efetch.fcgi?{urllib.parse.urlencode(params)}"
    return parse_efetch_xml(_get(url))


def rate_delay(cfg_eutils):
    time.sleep(0.11 if cfg_eutils.get("api_key") else 0.34)


def fetch_all(dump_only=False):
    cfg = load_config()
    cfg_eutils = cfg.get("pubmed", {})
    lookback_days = cfg_eutils.get("lookback_days", 3)

    journals = load_journals()
    st = state_mod.load_state()
    mindate, maxdate = state_mod.get_fetch_window(st, lookback_days)
    seen = state_mod.get_seen_set(st)

    log.info(f"[fetch] {len(journals)}개 저널, 기간 {mindate}~{maxdate}(edat)")

    all_pmids = {}
    for j in journals:
        try:
            pmids = esearch_journal(j["issn"], mindate, maxdate, cfg_eutils)
        except Exception as e:
            log.warning(f"[fetch] esearch 실패: {j['name']} ({e})")
            continue
        rate_delay(cfg_eutils)
        if dump_only:
            print(f"{j['name']:60s} ISSN={j['issn']}  hits={len(pmids)}")
            continue
        new_pmids = [p for p in pmids if p not in seen]
        for p in new_pmids:
            all_pmids[p] = j["name"]
        log.info(f"[fetch]   {j['name']}: {len(pmids)}건 조회, 신규 {len(new_pmids)}건")

    if dump_only:
        return

    if not all_pmids:
        log.info("[fetch] 신규 논문 없음")
        out_path = os.path.join(RAW_DIR, f"papers_{today_stamp()}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump([], f)
        return out_path

    pmid_list = list(all_pmids.keys())
    papers = []
    for i in range(0, len(pmid_list), 200):
        batch = pmid_list[i:i + 200]
        try:
            papers.extend(efetch_batch(batch, cfg_eutils))
        except Exception as e:
            log.error(f"[fetch] efetch 실패(batch {i}): {e}")
        rate_delay(cfg_eutils)

    for p in papers:
        p["source_journal_query"] = all_pmids.get(p["pmid"])

    out_path = os.path.join(RAW_DIR, f"papers_{today_stamp()}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)
    log.info(f"[fetch] {len(papers)}편 저장 -> {out_path}")
    return out_path


if __name__ == "__main__":
    fetch_all(dump_only="--dump" in sys.argv)
