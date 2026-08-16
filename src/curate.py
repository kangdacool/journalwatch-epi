##################################################
#####  CURATE: 주목도 판정 층 (토큰 사용, 판정 엔진 교체 가능)  #####
##################################################
"""
그날 raw 논문 배치를 rubric과 함께 프롬프트에 넣고 LLM에게 구조화 JSON 판정을 받는다.
판정 엔진은 config.yaml의 curation.backend로 고른다:

  "claude_cli" (기본) — 로컬 `claude --safe-mode -p` 헤드리스 호출. Claude Code 구독 로그인만
                        있으면 됨(API 키 불필요). Windows 작업 스케줄러 등 로컬 무인실행용.
  "anthropic_api"     — Anthropic API를 ANTHROPIC_API_KEY로 직접 호출. Claude Code 설치가
                        필요 없어 GitHub Actions 등 어디서나 돌아간다(토큰당 과금).

두 백엔드 모두 같은 RUBRIC·SCHEMA·build_prompt()를 쓴다 — 판정 로직은 하나, 호출 방식만 다르다.

사용법:
  python curate.py                 # 오늘자 raw 파일을 판정, assessments_<date>.json 저장
  python curate.py --dry-run       # 실제 호출 없이 프롬프트만 출력
  python curate.py --file <path>   # 특정 raw 파일을 판정(재실행/디버깅용)
"""

import os
import sys
import json
import glob
import subprocess

from common import load_config, get_logger, RAW_DIR, today_stamp

log = get_logger()

RUBRIC = """\
당신은 예방의학/역학 분야 신규 논문의 "주목도"를 판정하는 전문 심사자다. 임상 근거등급이 아니라
연구자 관점에서 이 논문이 왜 중요한지를 판단한다.

다음 네 범주 중 하나라도 명확히 해당하면 notable=true로 판정한다. 애매하면 false로(과다채록 방지).

A. 방법론적 참신성 — 새로운 추정량/식별전략, 기존 방법의 중요한 확장이나 비판, 새로운 인과추론
   응용, 편향보정 기법
B. 역학 이론에 대한 기여 — 새로운 개념적 프레임워크, 방법론 논쟁, 확립된 발견의 대규모 재현/반박
C. 대규모·독보적 자료원 — 신규 대규모 코호트 최초 결과, 바이오뱅크 규모 연계, 새로운 노출측정
   기법(웨어러블·위성·EHR 대규모 연계)
D. 정책적 파급력 — 가이드라인 변경 가능성, 정책결정에 바로 쓰일 질병부담/비용효과 추정

관심 태그(하드 필터 아님, 해당하면 표시만): {interest_tags}

각 논문마다 반드시:
- pmid: 입력의 pmid를 그대로
- notable: true/false
- categories: 해당하는 범주 코드 배열(A/B/C/D), notable=false면 빈 배열
- reasoning: 판정 근거 1-2문장(초록을 베끼지 말고 왜 주목할 만한지/아닌지를 설명)
- one_line_summary: 논문 내용 1줄 요약(notable=false여도 작성)
- interest_tags: 해당하는 관심 태그 배열(없으면 빈 배열)

초록에 없는 내용을 지어내지 마라. 판단 근거가 불충분하면 notable=false로 하고 이유에 "정보 부족"을
명시하라.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pmid": {"type": "string"},
                    "notable": {"type": "boolean"},
                    "categories": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["A", "B", "C", "D"]},
                    },
                    "reasoning": {"type": "string"},
                    "one_line_summary": {"type": "string"},
                    "interest_tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["pmid", "notable", "categories", "reasoning",
                             "one_line_summary", "interest_tags"],
            },
        }
    },
    "required": ["assessments"],
}

DEFAULT_INTEREST_TAGS = [
    "causal inference", "target trial emulation", "biological aging / epigenetic clocks",
    "chronic disease multimorbidity", "environmental epidemiology",
]


def latest_raw_file():
    files = sorted(glob.glob(os.path.join(RAW_DIR, "papers_*.json")))
    if not files:
        raise FileNotFoundError(f"{RAW_DIR}에 papers_*.json 없음 — 먼저 fetch_pubmed.py 실행")
    return files[-1]


def build_prompt(papers, interest_tags):
    rubric = RUBRIC.format(interest_tags=", ".join(interest_tags))
    lines = [rubric, "", f"아래 {len(papers)}편을 각각 판정하라. JSON으로만 응답하라.", ""]
    for p in papers:
        lines.append(f"--- pmid={p['pmid']} ---")
        lines.append(f"저널: {p.get('journal', '')}")
        lines.append(f"제목: {p.get('title', '')}")
        lines.append(f"저자: {', '.join(p.get('authors', [])[:3])}")
        lines.append(f"초록: {p.get('abstract', '(초록 없음)')}")
        lines.append("")
    return "\n".join(lines)


def call_claude_headless(prompt: str, cfg_curation: dict) -> dict:
    """
    --safe-mode: CLAUDE.md/스킬/훅/MCP 등 이 사용자의 방대한 전역 설정을 로드하지 않는다
    (2026-08-15 실측: --safe-mode 없이 호출하면 전역 CLAUDE.md 캐시생성만 5.7만 토큰 —
    호출당 $0.35. --safe-mode로는 고정 오버헤드 ~3.4만 토큰/$0.04로 축소). OAuth 로그인은
    그대로 쓴다(이 사용자는 API 키가 없고 구독 로그인만 있음 — --bare는 API 키 필수라 못 씀).
    응답의 structured_output 필드에 스키마 그대로 파싱된 객체가 들어온다(실측 확인,
    result 필드를 재파싱할 필요 없음).
    """
    model = cfg_curation.get("model", "claude-sonnet-5")
    max_budget = cfg_curation.get("max_budget_usd", 1.00)
    # 고정 600초는 큰 배치(실측: 270편에서 타임아웃)엔 부족 — config.yaml의
    # curation.timeout_seconds로 조절 가능하게(기본 1800초 = 30분).
    timeout_seconds = cfg_curation.get("timeout_seconds", 1800)
    # 프롬프트를 CLI 인자로 넘기면 Windows CreateProcess의 명령줄 길이 한도(약 32767자)를
    # 넘어 WinError 206으로 죽는다(실측 확인, 106편 배치에서 재현) — stdin으로 넘긴다.
    cmd = [
        "claude", "--safe-mode", "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(SCHEMA),
        "--model", model,
        "--max-budget-usd", str(max_budget),
    ]
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                             encoding="utf-8", timeout=timeout_seconds)
    if result.returncode != 0:
        raise RuntimeError(f"claude -p 실패(rc={result.returncode}): {result.stderr[:2000]}")
    outer = json.loads(result.stdout)
    if outer.get("is_error"):
        raise RuntimeError(f"claude -p 오류 응답: {outer.get('errors') or outer.get('result')}")
    return outer["structured_output"]


def call_anthropic_api(prompt: str, cfg_curation: dict) -> dict:
    """
    Anthropic Messages API를 도구강제(tool_choice) 방식으로 호출해 구조화 JSON을 받는다 —
    claude_cli 백엔드의 --json-schema와 동등한 효과(모델이 반드시 이 스키마의 도구를 호출하게
    강제). Claude Code 설치·구독 없이 ANTHROPIC_API_KEY 하나로 어디서나 돌아간다(GitHub Actions
    포함) — 그래서 공개 저장소의 기본 경로다. ⚠ 이 사용자는 API 키가 없어 실계정으로 실행
    검증은 못 했다(코드는 문서화된 표준 패턴 그대로) — 처음 쓰는 사람은 반드시 --dry-run 대신
    소량 배치로 먼저 실제 호출해 볼 것.
    """
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 없습니다 — anthropic_api 백엔드에 필수")

    model = cfg_curation.get("model", "claude-sonnet-5")
    max_tokens = cfg_curation.get("max_tokens", 8000)
    client = anthropic.Anthropic(api_key=api_key)

    tool = {
        "name": "submit_assessments",
        "description": "각 논문의 주목도 판정을 제출한다.",
        "input_schema": SCHEMA,
    }
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        tools=[tool],
        tool_choice={"type": "tool", "name": "submit_assessments"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "submit_assessments":
            return block.input
    raise RuntimeError(f"submit_assessments 도구 호출을 찾지 못함: {resp.content}")


BACKENDS = {
    "claude_cli": call_claude_headless,
    "anthropic_api": call_anthropic_api,
}


def curate(papers, cfg, dry_run=False):
    cfg_curation = cfg.get("curation", {})
    interest_tags = cfg_curation.get("interest_tags", DEFAULT_INTEREST_TAGS)

    if dry_run:
        print(build_prompt(papers, interest_tags))
        return None

    backend_name = cfg_curation.get("backend", "claude_cli")
    if backend_name not in BACKENDS:
        raise ValueError(f"알 수 없는 curation.backend={backend_name!r} — {list(BACKENDS)} 중 하나")

    # 한 프롬프트에 너무 많은 논문을 몰아넣으면(실측: 270편 한 배치, ~40만자) 응답이 10분을
    # 넘겨도 안 끝나거나 원인불명 실패(rc=1, 빈 stderr)로 죽는다 — chunk_size(기본 40)로 쪼개서
    # 순차 호출한다. 이례적으로 큰 배치(초기 백필 등)에 특히 중요.
    chunk_size = cfg_curation.get("chunk_size", 40)
    all_assessments = []
    chunks = [papers[i:i + chunk_size] for i in range(0, len(papers), chunk_size)]
    for i, chunk in enumerate(chunks, 1):
        log.info(f"[curate] 배치 {i}/{len(chunks)} 판정 중 ({len(chunk)}편)...")
        prompt = build_prompt(chunk, interest_tags)
        payload = BACKENDS[backend_name](prompt, cfg_curation)
        all_assessments.extend(payload.get("assessments", []))
    return all_assessments


if __name__ == "__main__":
    cfg = load_config()
    dry = "--dry-run" in sys.argv
    if "--file" in sys.argv:
        path = sys.argv[sys.argv.index("--file") + 1]
    else:
        path = latest_raw_file()
    with open(path, "r", encoding="utf-8") as f:
        papers = json.load(f)
    log.info(f"[curate] {len(papers)}편 판정 시작 ({'dry-run' if dry else '실제 호출'}) — {path}")
    assessments = curate(papers, cfg, dry_run=dry)
    if assessments is None:
        sys.exit(0)
    out_path = os.path.join(RAW_DIR, f"assessments_{today_stamp()}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(assessments, f, ensure_ascii=False, indent=2)
    n_notable = sum(1 for a in assessments if a.get("notable"))
    log.info(f"[curate] {len(assessments)}편 판정 완료, notable={n_notable} -> {out_path}")
