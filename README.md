# Prompt Quality Monitor

Claude Code 세션 기록에서 **내가 실제로 타이핑한 프롬프트**만 뽑아, Thorgeirsson·Weidmann·Su
(CHI '26) 「Computer Science Achievement and Writing Skills Predict Vibe Coding Proficiency」의
전문가 루브릭으로 점수화한다.

새로 데이터를 쌓을 필요가 없다. `~/.claude/projects`에 이미 전부 남아 있다.

## 무엇을 하는가

1. **추출** — 세션 JSONL에서 `promptSource="typed"` + `origin.kind="human"`인 레코드만 고른다.
   도구 결과, 붙여넣기, 에이전트 간 peer 메시지, SDK 트래픽, 큐 재생은 전부 `type:"user"`로
   들어오지만 사람이 친 것이 아니다. 이 필터가 틀리면 아래 전부가 틀린다.

2. **분류** — 프롬프트를 6종으로 나눈다: 과제 개시 / 후속 지시 / 질문 / 정정 / 승인 / 상태 보고.
   이게 점수 자체보다 중요하다. 실제 코퍼스의 상당수가 "응", "오케이 진행해줘" 같은 승인 턴인데,
   이걸 구체성 0점으로 집계하면 점수가 실력이 아니라 **말투**를 측정하게 된다.
   한국어의 정중한 의문형("고쳐줄 수 있어?")과 영어의 같은 형태("can you fix X?")는
   질문이 아니라 지시로 분류한다. 두 언어 모두 실제 문장으로 검증했다.

3. **채점** — 두 레이어.
   - *결정론적* (무료, 전량): 구체성 / 구조화 / 제약 명시. 절대 임계값이 아니라 **내 코퍼스 안의
     백분위**로 환산한다. "이 프롬프트가 충분히 구체적인가"의 절대 기준은 지어낼 수밖에 없지만,
     "내가 쓴 다른 3,900개와 비교해 어디쯤인가"는 실제로 답할 수 있는 질문이다.
   - *LLM 루브릭* (표본): 논문의 전문가 루브릭 3축 — coherence / task-appropriate complexity /
     instructional clarity — 을 그대로 적용하고, 문제점 한 줄과 개선안을 함께 받는다.

4. **어휘 다양성** — MTLD와 HD-D (McCarthy & Jarvis 2010). 논문이 성과와 유의한 상관을 보고한
   두 지표(r = .343 / .310)로, 길이에 독립적이라 짧은 텍스트에 쓸 수 있다. 세션 단위로 측정한다
   (논문도 task attempt 단위로 프롬프트 시퀀스 전체를 하나로 채점했다).

5. **상관 분석** — 프롬프트 점수가 **재작업**을 줄이는지 검증한다.

## 결과 대리 지표에 관한 정직한 경고

논문은 프롬프트 품질을 *채점된 결과물*과 상관지었다 (r = .479). 세션 로그에는 그 정답이 없다.
이 도구가 쓰는 대리 지표는 **재작업** — 다음 한두 턴 안에 사람이 정정을 넣었는지 여부다.

이건 상한이 낮은 지표다. 재작업은 프롬프트 품질뿐 아니라 과제 난이도, 환경 문제, 모델 실수에도
같이 반응한다. 그래서 아래 r 값은 전부 하한으로 읽어야 하고, 대시보드와 리포트는 유의하지 않은
결과도 전부 싣는다.

그럼에도 이 대리 지표로 신호가 잡혔다 (저자 코퍼스: 프롬프트 약 3,900개, 채점 표본 330개):

| 관계 | r | p | n |
|---|---:|---:|---:|
| LLM 루브릭 점수 → 직후 재작업 | **−0.165** | .003 | 330 |
| LLM 지시 명확성 → 직후 재작업 | **−0.145** | .008 | 330 |
| 정규식 점수 → 직후 재작업 | +0.009 | .64 | 2,436 |
| LLM 루브릭 ↔ 정규식 (두 채점의 일치도) | +0.020 | .72 | 330 |

읽는 법은 이렇다. **루브릭 채점은 재작업을 예측하고, 정규식 지표는 못 한다.** 그리고 둘의 일치도가
r = .02로 사실상 없다 — 경로·식별자·수치를 세는 일과 "지시가 명확한가"는 서로 다른 것을 재고 있다.
논문에서 인간 전문가 채점(r = .479)이 어휘 다양성 지표(r = .31–.34)를 크게 앞섰던 것과 같은 패턴이다.

따라서 정규식 층은 **예측기가 아니라 습관 프로파일러**로 쓰는 것이 맞다. 어떤 모호어를 쓰는지,
제약을 얼마나 명시하는지를 전량·무료로 보여주는 용도다. 품질 판정에는 루브릭 채점을 써야 한다.

측정을 짤 때 실제로 걸린 함정 하나를 기록해 둔다. 처음에는 세션 어휘 다양성을 *모든* 턴으로
계산했더니 HD-D와 재작업률이 유의하게 양의 상관(r = +.202, p = .008)으로 나왔다. 원인은 순환이었다 —
정정 턴은 에러 메시지와 새 용어를 끌고 들어오므로, 재작업이 많은 세션이 그 이유만으로 어휘가
풍부해 보인다. 지시 턴만으로 다시 계산하니 r = +.006으로 사라졌다.

## 설치

의존성이 0개다. 전부 파이썬 표준 라이브러리라 시스템에 깔린 `python3`로 그대로 돈다.
pip도 venv도 필요 없고, 사내 프록시에 pip이 막혀 있어도 상관없다.

### Claude Code

```
/plugin marketplace add yunhui-chae-krafton/prompt-quality-monitor
/plugin install prompt-quality-monitor@prompt-quality-monitor
```

### Codex

```bash
codex plugin marketplace add yunhui-chae-krafton/prompt-quality-monitor
codex plugin add prompt-quality-monitor@prompt-quality-monitor
```

### OpenCode

OpenCode의 plugin은 npm JS 모듈이라 체계가 다르다. 스킬로 붙인다.

```bash
git clone https://github.com/yunhui-chae-krafton/prompt-quality-monitor ~/prompt-quality-monitor
ln -s ~/prompt-quality-monitor/skills/prompt-score ~/.config/opencode/skills/prompt-score
```

스크립트가 스킬 디렉토리 안에 들어 있어서 심볼릭 링크 하나로 코드까지 따라온다.

### 아무것도 설치하지 않고

```bash
git clone https://github.com/yunhui-chae-krafton/prompt-quality-monitor
cd prompt-quality-monitor
python3 pqm.py analyze
```

### 왜 매니페스트가 두 벌인가

`.claude-plugin/marketplace.json`과 `.agents/plugins/marketplace.json`이 같은 내용을 담고
있다. Claude Code와 Codex가 `source` 필드에서 갈리기 때문이다 — Claude Code는 문자열
`"./"`를 받고 `{"source":"local","path":"./"}`를 거부하며, Codex는 정반대로 문자열
형태에서 플러그인을 0개로 해석한다. 한 형태로 둘 다 만족시킬 수 없어서 각자 자기 위치에서
자기 모양을 읽게 했다. Codex는 `.agents/plugins/`를 먼저 보고 없을 때만 `.claude-plugin/`으로
내려간다.

### 선택: 한국어 형태소 분석기

```bash
pip install kiwipiepy
```

없어도 동작한다 (조사·어미를 벗기는 정규식 근사로 대체). 다만 `알려드리면`과 `알려주고`를
같은 낱말로 접지 못해 어휘 다양성이 부풀어 오른다 — 같은 코퍼스에서 세션 MTLD 중앙값이
78.9(형태소) 대 189.7(정규식)로 2.4배 차이가 났다. 세션 간 비교는 폴백으로도 유효하지만,
절대값을 문헌과 견주려면 필요하다.

## 사용

```bash
python3 pqm.py analyze                 # 결정론적 지표, 전량, 무료
python3 pqm.py analyze --print-report   # 리포트를 표준출력으로
python3 pqm.py dashboard                # HTML 대시보드
```

출력은 `~/.prompt-quality-monitor/`에 쌓인다 (`analysis.json`, `report.md`, `dashboard.html`,
`llm-cache.json`). `--out` 또는 `$PQM_OUT`으로 바꿀 수 있다. 플러그인 설치 디렉토리는 버전이
올라갈 때마다 통째로 교체되므로, 애써 만든 채점 캐시를 거기 두지 않으려고 홈에 쓴다.

범위는 `--project <부분일치>`와 `--since YYYY-MM-DD`로 좁힌다.

## 루브릭 채점: 백엔드 세 가지

특정 벤더의 CLI에 채점을 묶어두면, 그 구독이 없는 사람은 이 층 전체를 못 쓴다. 그래서
채점기를 갈아끼울 수 있게 했다.

**`--backend agent` (기본).** 채점 요청 파일을 쓰고 멈춘다. 스킬을 실행 중인 에이전트가
자기 세션 안에서 채점하고 결과를 돌려준다. **추가 비용도 API 키도 별도 구독도 없다** —
읽을 주체가 이미 거기 있기 때문이다. 어떤 제품에서든 스킬만 돌면 동작한다.

```bash
python3 pqm.py grade --limit 240      # 요청 생성 → 에이전트가 채점
python3 pqm.py grade --ingest         # 결과 반영
```

요청은 `{rubric, items:[{id, prompt}]}`이고 응답은
`[{id, coherence, complexity, clarity, issue, rewrite}]`다. `id`는 내용 해시라서 순서가
바뀌어도, 일부만 채점해도, 여러 번에 나눠 해도 정확히 병합된다.

**`--backend claude`.** `claude` CLI로 무인 채점. 20개 배치당 약 $0.18 (Sonnet 5, MCP 비활성),
240개면 약 $2.

**`--backend command`.** stdin으로 프롬프트를 받고 stdout으로 JSON 배열을 내는 아무 명령에나
위임한다. 다른 에이전트 CLI, 로컬 모델, 사내 게이트웨이 래퍼가 다 여기 들어온다.

```bash
python3 pqm.py grade --backend command --command 'codex exec -'
python3 pqm.py grade --backend command --command 'ollama run qwen3'
```

어느 백엔드를 쓰든 결과는 프롬프트 해시로 같은 캐시에 쌓이므로, 재실행은 새 프롬프트에만
비용이 든다. `analyze`만 쓰면 네트워크 호출이 0이다.

**표본 추출.** 무작위가 아니라 점수 10분위 층화다. 무작위로 뽑으면 표본이 분포 한가운데로 쏠려
정작 알고 싶은 양 끝단에 대해 아무 말도 못 한다.

## 프라이버시

`analyze`와 `dashboard`는 네트워크 호출이 전혀 없다. 세션 로그를 읽어 로컬에 파일을 쓸 뿐이다.

채점은 백엔드에 따라 다르다. 기본값인 `--backend agent`는 **프롬프트가 지금 쓰고 있는 세션 밖으로
나가지 않는다** — 이미 그 대화 안에 있는 에이전트가 채점한다. `--backend claude`나
`--backend command`를 쓰면 프롬프트 원문이 해당 백엔드로 나가므로, 사내 정보가 담긴 프롬프트를
포함한다는 점을 알고 골라야 한다.

## 구조

```
pqm.py                        루트 런처 (clone 후 바로 실행용)
.claude-plugin/
  plugin.json                 플러그인 매니페스트 (Claude Code·Codex 공용)
  marketplace.json            Claude Code용 마켓플레이스
.agents/plugins/
  marketplace.json            Codex용 마켓플레이스
skills/prompt-score/
  SKILL.md                    진입점 + 채점 절차 + 결과 해석 규칙
  scripts/pqm.py              CLI
  scripts/pqm/extract.py      세션 JSONL → 사람이 친 프롬프트
  scripts/pqm/rubric.py       턴 분류 + 원시 특징 추출
  scripts/pqm/lexical.py      MTLD / HD-D + 한국어 토크나이저
  scripts/pqm/analyze.py      백분위 환산, 세션 집계, 상관·편상관
  scripts/pqm/llm_grade.py    채점 백엔드 3종 + 캐시
  scripts/pqm/report.py       마크다운 리포트
  scripts/pqm/dashboard.py    자체 완결 HTML 대시보드
```

스크립트가 `skills/prompt-score/` 안에 있는 것은 의도한 배치다. 스킬 디렉토리만 심볼릭
링크해도 코드가 함께 따라오므로, 플러그인 체계가 없는 하네스(OpenCode 등)에서도 그대로 돈다.

## 알려진 한계

- **재작업 탐지가 검증되지 않았다.** 한국어 정정 표현("여전히", "그게 아니라", "안 되는데")의
  정규식 매칭이고, 재현율·정밀도를 사람이 채점해 확인한 적이 없다. 이걸 검증하는 것이 다음 단계로
  가장 값어치 있는 작업이다.
- **백분위 점수의 평균은 정의상 5다.** 월별·주별 값은 절대 실력 변화가 아니라 그 기간의 프롬프트가
  전체 대비 어디였는지를 뜻한다. 절대 기준이 필요하면 LLM 축(0–10)을 봐야 한다.
- **LLM 채점은 맥락 없이 이뤄진다.** 직전 대화를 주지 않으므로, 맥락에 의존하는 짧은 후속 지시가
  실제보다 낮게 평가될 수 있다. 프롬프트에 그 점을 감안하라고 명시해 뒀지만 완전히 상쇄되지는 않는다.
- **언어는 한국어·영어만 확인했다.** 그 밖의 언어에서는 승인·상태 보고·정중 명령형 분리가
  동작하지 않고, 그러면 지시 턴 풀이 오염돼 점수 하한이 무너진다.
- **다중 비교 보정을 하지 않았다.** 17개 상관을 동시에 보고하므로 p < .05 하나쯤은 우연히 나온다.
  다만 위 표의 LLM 관련 결과는 사전에 세운 가설(논문의 인간 채점 축)에 대한 검정이고 p = .003이므로,
  Bonferroni(α = .05/17 ≈ .003)를 적용해도 경계선상에 남는다.
