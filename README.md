# 📚 journalwatch-epi

**저널을 매일 훑어보는 대신, LLM에게 "이게 왜 중요한지" 판단을 맡기는 개인 연구 도구입니다.**

지정한 저널 + Scimago 카테고리 상위 쿼타일에서 신규 논문을 PubMed로 매일 수집하고, 방법론적
참신성·이론적 기여·대규모 자료·정책 파급력 기준으로 LLM이 판정한 뒤, "주목할 만한" 논문만 로컬
마크다운(Obsidian-ready)과 Notion 다이제스트로 정리합니다. 원래 예방의학/역학 분야용으로 만들었지만
`config.yaml`만 바꾸면 다른 분야에도 그대로 씁니다.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

---

## 왜 만들었나

매일 아침 관심 저널을 열어보는 건 지속 가능하지 않고, 그렇다고 전체 목차를 다 훑는 건 시간
낭비입니다. 이 도구는 그 사이 어딘가를 겨냥합니다 — **기계적으로 할 수 있는 일**(저널·날짜별
수집)은 무료 API로 처리하고, **판단이 필요한 일**(이 논문이 왜 중요한가)만 LLM에 맡깁니다.

## 비슷한 도구들과 차이

이 분야에 이미 여러 도구가 있습니다. 아래는 실제로 코드/문서를 확인하고 정리한 비교입니다.

| 도구 | 접근 | 이 도구와 차이 |
|---|---|---|
| [PubCrawler](https://pubcrawler.gen.tcd.ie/)(1999~) | MeSH/저널/키워드 기반 PubMed 이메일 알림 | LLM 판단 없음 — "새 논문"만 걸러줄 뿐 "왜 중요한지"는 안 알려줌 |
| [ArxivDigest](https://github.com/AutoLLM/ArxivDigest) | GPT로 각 논문에 1-10점 채점, 이메일/HTML 발송 | arXiv 전용. 점수만 주고 근거 설명이 약함(이 도구는 범주+1-2문장 근거를 남김) |
| [arxiv-sanity-lite](https://github.com/karpathy/arxiv-sanity-lite)(Karpathy) | 사용자가 태그한 논문으로 TF-IDF/SVM 학습, LLM 호출 없음 | 훨씬 저렴하지만 "왜 중요한가"라는 설명력이 없음 — 다른 철학 |
| **journalwatch-epi** | PubMed 수집(무료) + LLM 판정(범주 A/B/C/D + 근거) + 로컬 마크다운/Notion | 판정 근거를 남기고, Scimago 쿼타일로 "지정 저널 밖" 상위 저널도 안전망으로 포함 |

## 두 가지 실행 방식

### 1) 로컬 (Claude Code CLI, 구독만 있으면 됨 — API 과금 없음)

Claude Code에 로그인돼 있다면 API 키 없이 그대로 씁니다. Windows 작업 스케줄러로 매일 자동
실행하도록 만들어졌습니다(다른 OS는 cron으로 대체 가능, 아래 스크립트는 그대로 재사용).

```bash
git clone https://github.com/kangdacool/journalwatch-epi.git
cd journalwatch-epi
pip install -r requirements.txt
playwright install chromium   # journals.py(저널 목록 갱신)에만 필요, 일일 실행엔 불필요
cp config.example.yaml config.yaml   # 이메일·저널 목록 등 채우기
python src/journals.py               # 저널 목록 최초 생성(캐시, 기본 90일마다만 재실행)
python src/run_daily.py --dry-run    # 프롬프트만 확인
python src/run_daily.py              # 실제 실행
```

작업 스케줄러 등록(PowerShell):

```powershell
$action = New-ScheduledTaskAction -Execute "C:\path\to\journalwatch-epi\run_daily.bat"
$trigger = New-ScheduledTaskTrigger -Daily -At 7:00am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "journalwatch-epi" -Action $action -Trigger $trigger -Settings $settings
```

### 2) GitHub Actions (API 키 필요, Claude Code 설치·구독 불필요)

`config.yaml`의 `curation.backend: "anthropic_api"`로 바꾸거나, Actions 워크플로가 자동으로
그렇게 설정합니다(`.github/workflows/daily.yml` 참조). 이 방식은 **Claude Code 설치 없이
API 키 하나로 어디서나 돌아갑니다** — 이 저장소를 fork한 뒤:

1. 저장소 **Settings → Secrets and variables → Actions**에서:
   - Secret `ANTHROPIC_API_KEY` (필수)
   - Secret `NOTION_TOKEN` (선택, Notion 다이제스트를 쓸 경우)
   - Variable `PUBMED_EMAIL` (권장, NCBI 예의 규정)
   - Variable `NOTION_PARENT_PAGE_ID` (선택)
2. `.github/workflows/daily.yml`의 `schedule:` 줄 주석을 푸세요 — **기본적으로 꺼져 있습니다**
   (원저자는 로컬 `claude_cli` 경로를 쓰므로, secrets 없이 이 저장소만 fork해도 매일 무인
   실행되다가 키 없음으로 실패 메일만 쌓이는 걸 막기 위함). 1번을 먼저 하고 풀 것.
3. **Actions** 탭에서 워크플로를 한 번 수동 실행(`workflow_dispatch`)해 확인
4. 이후 매일 자동 실행 — **CI는 매번 새 디스크로 시작하므로, 실행마다 `data/seen.json`과
   `archive/papers/*.md`를 저장소에 커밋해 되돌려 넣습니다. 이 저장소 자체가 곧 당신의
   아카이브가 됩니다** (fork니까 당신 것입니다).

⚠️ `anthropic_api` 백엔드는 표준 문서화된 패턴(도구강제 구조화 출력)으로 작성했지만, 저는
API 키가 없어(Claude Code 구독만 사용) 실계정으로 끝까지 검증하지는 못했습니다. 처음 쓰신다면
소량 배치로 먼저 실제 호출해보세요(`python src/curate.py --file data/raw/papers_YYMMDD.json`).

## 다른 분야로 커스터마이즈

`config.yaml`의 `journals.named`(지정 저널 목록)와 `journals.scimago_category_id`만 바꾸면
됩니다. 카테고리 id는 [scimagojr.com/journalrank.php](https://www.scimagojr.com/journalrank.php)에서
원하는 분야를 고르면 URL의 `category=NNNN`으로 나옵니다. 판정 rubric은 `src/curate.py`의
`RUBRIC` 문자열 — 지금은 "방법론적 참신성/이론적 기여/대규모 자료/정책 파급력" 네 범주지만
분야에 안 맞으면 자유롭게 바꾸세요.

`journals.py`(Scimago 스크레이핑)는 Cloudflare를 우회하기 위해 **실제 Chrome을 CDP로 띄웁니다**
— 브라우저가 필요해 CI에서 매일 돌리기엔 무겁습니다. 로컬에서 가끔(기본 90일) 실행해
`data/journals.json`을 갱신하고 커밋하면, 일일 워크플로는 그 캐시만 읽습니다.

## 아키텍처

```
src/journals.py       저널 목록 결정(지정 목록 ∪ Scimago 쿼타일) → data/journals.json (로컬/가끔)
src/fetch_pubmed.py    PubMed E-utilities로 신규 논문 수집(무료, 토큰 0) → data/raw/papers_*.json
src/curate.py          LLM 판정(claude_cli 또는 anthropic_api 백엔드, 동일 rubric)
src/archive.py         notable 논문 → archive/papers/*.md (frontmatter, Obsidian-ready)
src/notion_sync.py     그날의 다이제스트를 Notion 페이지로 push(선택)
src/run_daily.py       위 전부를 순서대로 실행하는 오케스트레이터
```

각 단계는 파일로 통신합니다(중간 상태를 메모리로 넘기지 않음) — 한 단계가 실패해도 이전
단계의 산출물은 남아 다음 실행에서 재시도됩니다. `data/seen.json`이 성공한 실행에서만
갱신되므로, 실패한 실행은 자동으로 재시도 대상이 됩니다.

## Notion 연동 (선택)

1. [notion.so/my-integrations](https://www.notion.so/my-integrations)에서 Internal
   Integration 생성 → 토큰을 `.env`에 `NOTION_TOKEN=...`로 저장
2. 다이제스트를 받을 페이지를 만들고 "···" → 연결 → 방금 만든 integration 공유
3. 그 페이지 id를 `config.yaml`의 `notion.parent_page_id`(또는 GitHub Actions Variable
   `NOTION_PARENT_PAGE_ID`)에 기록

비워두면 Notion push는 자동으로 건너뛰고 로컬 마크다운 아카이브만 쌓입니다.

## License

MIT — [LICENSE](LICENSE) 참조.
