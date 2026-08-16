##################################################
#####  COMMON: 경로 · 설정 · 로깅 유틸  #####
##################################################
"""프로젝트 전역에서 쓰는 경로/설정/로그 헬퍼. chungyak-watch의 common.py 패턴을 재사용."""

import os
import sys
import logging
from datetime import datetime

import yaml

# Windows 기본 콘솔(cp949)에서 유니코드 문자 출력 시 UnicodeEncodeError로 실행이
# 중단되는 것을 방지 — stdout/stderr를 utf-8(+replace)로 고정.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ----- 경로 -----
PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJ_ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
LOGS_DIR = os.path.join(PROJ_ROOT, "logs")
ARCHIVE_DIR = os.path.join(PROJ_ROOT, "archive")
PAPERS_DIR = os.path.join(ARCHIVE_DIR, "papers")
CONFIG_PATH = os.path.join(PROJ_ROOT, "config.yaml")

for _d in (DATA_DIR, RAW_DIR, LOGS_DIR, ARCHIVE_DIR, PAPERS_DIR):
    os.makedirs(_d, exist_ok=True)


# ----- 설정 로드 -----
# 개인 식별정보(이메일 등)나 GitHub Actions 같은 CI 환경에서 바꿔야 하는 값은 config.yaml에
# 커밋하는 대신 환경변수로 덮어쓸 수 있게 한다 — 공개 저장소에 개인정보를 박아넣지 않기 위함
# (git_oss_collaboration.md 컨벤션: "개인 식별정보는 환경변수로 뺀다").
_ENV_OVERRIDES = {
    "JOURNALWATCH_PUBMED_EMAIL": ("pubmed", "email"),
    "JOURNALWATCH_NOTION_PARENT_PAGE_ID": ("notion", "parent_page_id"),
    "JOURNALWATCH_CURATION_BACKEND": ("curation", "backend"),
}


def load_config(path: str = CONFIG_PATH) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"설정 파일이 없습니다: {path}\n"
            f"config.example.yaml 를 복사해 config.yaml 을 만들고 값을 채우세요."
        )
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    for env_key, (section, field) in _ENV_OVERRIDES.items():
        val = os.environ.get(env_key)
        if val:
            cfg.setdefault(section, {})[field] = val
    return cfg


# ----- 로깅 -----
def get_logger(name: str = "journalwatch") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    stamp = datetime.now().strftime("%y%m%d")
    fh = logging.FileHandler(os.path.join(LOGS_DIR, f"run_{stamp}.log"), encoding="utf-8")
    ch = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (fh, ch):
        h.setFormatter(fmt)
        logger.addHandler(h)
    return logger


def today_stamp() -> str:
    return datetime.now().strftime("%y%m%d")
