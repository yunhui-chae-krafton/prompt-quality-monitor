"""Turn classification and deterministic prompt features.

Two jobs, kept separate on purpose:

1. `classify` decides what kind of turn a prompt is. This matters more than
   any scoring detail. A large share of real prompts are bare approvals
   ("응", "오케이 진행해줘") or status reports ("로그인 완료했어"). Scoring those
   for specificity produces noise and punishes a speech habit rather than a
   skill, so they are excluded from the rubric and counted separately.

2. `features` extracts raw, continuous signals. It deliberately does NOT emit
   a 0-10 score: thresholds picked in advance are arbitrary. `analyze` turns
   these into percentile ranks against the author's own corpus instead.

The three rubric axes mirror the expert rubric in Thorgeirsson et al.
(coherence, task-appropriate complexity, instructional clarity), recast as
things a regex can actually see.
"""

import re

from .lexical import tokenize

# --------------------------------------------------------------------------
# Turn classification
# --------------------------------------------------------------------------

# Approval turns hand control back without adding an instruction. The whole
# string has to be approval words for this to match, so "good, but change the
# header" stays an instruction while "looks good, ship it" does not.
_APPROVE_WORD = (
    r"응+|어+|넵|네+|예|ㅇㅇ+|ㅇㅋ+|오케이|오키|오케|굿|ㄱ{2,}|고고|고|"
    r"그래|그렇지|그렇게|그대로|좋아|좋(습니다|네요)|맞아|맞지|완벽|땡큐|"
    r"진행(해\s*줘|해|하자)?|해\s*줘|해줘요|하자|가자|계속(해\s*줘|해)?|"
    r"반영(해\s*줘|해)?|부탁(해|해\s*줘|드려요)?|"
    r"(hi)+|hello|안녕|thanks|thx|"
    r"ok(ay)?|y(es|ep|up)?|sure|great|nice|cool|awesome|perfect|good|fine|"
    r"looks?|sounds?|seems?|agreed|exactly|right|correct|true|"
    r"go|going|ahead|please|do|it|that|ship|send|continue|proceed|keep|on|"
    r"makes?|sense|works?|lgtm|\+1"
)
_APPROVE = re.compile(
    rf"^({_APPROVE_WORD})([\s,.!~]+({_APPROVE_WORD}))*[\s,.!~여요아]*$",
    re.IGNORECASE,
)

# Pure continuations: they hand control back without adding an instruction.
_CONTINUE = re.compile(
    r"^(자|그럼|그러면|이제|ㅇㅋ|오케이|응)?\s*"
    r"(계속|이어서|마저|나머지)\s*"
    r"(해\s*줘|해|진행(해\s*줘|해)?|가자|하자|부탁해?)?[\s.!~요]*$"
)

_CONTINUE_EN = re.compile(
    r"^(ok(ay)?|alright|right|so|then|now)?[\s,]*"
    r"(continue|proceed|carry\s+on|keep\s+going|go\s+on|go\s+ahead|"
    r"keep\s+at\s+it|next)"
    r"[\s.!~]*$",
    re.IGNORECASE,
)

_STATUS = re.compile(
    r"^.{0,40}(완료했|다\s*했|했어|끝냈|됐어|됬어|성공했|설치했|로그인했|인증했|"
    r"켰어|열었어|눌렀어|보냈어|올렸어|"
    r"done|finished|it'?s\s+(done|up|running)|i\s+(did|ran|installed|logged\s+in|"
    r"restarted|pushed|merged|approved)\b.*)[\s.!~어요으음ㅇ]*$",
    re.IGNORECASE
)

# Korean politeness turns imperatives into interrogatives: "이거 다시 해볼래?"
# and "png로 줄 수 있어?" are instructions, not questions. The request ending
# is the reliable signal, since it only ever attaches to an action verb.
_POLITE_IMPERATIVE = re.compile(
    r"(줄래|줄\s*수\s*있|줄\s*래|볼래|주라|주세요|주실|주시겠|주면\s*좋겠|해\s*줄|"
    # English wraps the same imperative in a question: "can you fix X?" asks for
    # an action, not an answer.
    r"\b(can|could|would|will)\s+you\b|\bplease\b|\blet'?s\b|"
    r"\b(i'?d\s+like|i\s+want|i\s+need)\s+(you\s+)?to\b)",
    re.IGNORECASE,
)

# Unambiguous rework signals. These stand on their own.
_CORRECT_STRONG = re.compile(
    r"(여전히|아직도|또\s*같은|틀렸|잘못\s*(됐|되|했|나)|되돌리|되돌려|롤백|revert|"
    r"그게\s*아니|아니라\s|아니고\s|다시\s*(해\s*줘|해줘|만들|짜|봐\s*줘)|"
    r"에러\s*(나|떠|가\s*나)|오류\s*(나|떠)|실패했|깨졌|깨져|망가|"
    r"안\s*[돼되]는데|안[돼되]는데|안\s*됐|"
    r"still\s+(not|fail|broken|the\s+same)|not\s+work|doesn'?t\s+work|"
    r"that'?s\s+not\s+what|not\s+what\s+i\s+meant|\brevert\b|\bundo\b|"
    r"\brollback\b|roll\s+back|went\s+wrong|broke\s+again|same\s+error)",
    re.IGNORECASE,
)

# Utterance-initial 아니 is a correction; 아니야? at the end is a tag question.
_CORRECT_INITIAL = re.compile(r"^(흠\s*)?아니[\s,]")

# Weaker signals: only a correction when the turn is not itself a question.
_CORRECT_WEAK = re.compile(
    r"(안\s*[돼되]|않[는아어]|왜\s*안|왜\s*이렇게|왜\s*그렇|wrong)",
    re.IGNORECASE,
)

# Genuine information requests: they ask for an answer, not an action.
_QUESTION = re.compile(
    r"(뭐야|뭐지|뭔가요|뭡니까|뭔지|무엇|어떤\s*게|어느\s*게|어디에|어디서|언제|누가|"
    r"왜\s|어떻게\s*(해야|하는|되|돼)|가능해|가능한|가능할까|"
    r"할\s*수\s*있(나|을까)|맞나|맞아\?|맞지\?|거지\?|인가\?|일까|될까|할까|"
    r"차이가?\s*뭐|어때|괜찮을까|필요할까|"
    r"\bwhat\s+(is|are|does|do|would)\b|\bwhy\b|\bwhich\b|\bwhere\b|"
    r"\bwhen\b|\bwho\b|\bhow\s+(does|do|is|are|come)\b|"
    r"\bis\s+(it|there|this|that)\b|\bare\s+(there|these|those)\b|"
    r"\bdo\s+(you\s+)?(know|think)\b|difference\s+between)",
    re.IGNORECASE,
)

_ENDS_QUESTION = re.compile(r"\?\s*$")


def classify(text, turn_index):
    """Return one of: approve, status, correct, question, initiate, refine.

    Order is deliberate. Approvals are caught first because they are short and
    unambiguous; polite imperatives are resolved before the question test so
    that "고쳐줄 수 있어?" lands in the instruction bucket where it belongs.
    """
    compact = re.sub(r"\s+", " ", text.strip())

    if len(compact) <= 45:
        if (_APPROVE.match(compact) or _CONTINUE.match(compact)
                or _CONTINUE_EN.match(compact)):
            return "approve"
        if _STATUS.match(compact):
            return "status"

    if _CORRECT_STRONG.search(compact) or _CORRECT_INITIAL.match(compact):
        return "correct"

    asks_action = bool(_POLITE_IMPERATIVE.search(compact))
    ends_q = bool(_ENDS_QUESTION.search(compact))
    is_question = bool(_QUESTION.search(compact)) or ends_q

    if is_question and not asks_action:
        if _CORRECT_WEAK.search(compact) and not ends_q:
            return "correct"
        return "initiate" if turn_index == 0 else "question"

    if _CORRECT_WEAK.search(compact) and not ends_q:
        return "correct"

    return "initiate" if turn_index == 0 else "refine"


#: Turn kinds that carry an instruction and therefore get the rubric.
SCORED_KINDS = ("initiate", "refine")

#: Kinds where the human is reacting rather than instructing.
REACTIVE_KINDS = ("approve", "status", "correct", "question")

# --------------------------------------------------------------------------
# Feature extraction
# --------------------------------------------------------------------------

_VAGUE_TOKENS = {
    "그거", "그것", "이거", "이것", "저거", "저것", "요거", "거기", "저기",
    "뭐", "뭔가", "무언가", "좀", "잘", "대충", "적당히", "알아서", "그냥",
    "아무", "아무거나", "예쁘게", "이쁘게", "깔끔하게", "제대로", "어떻게든",
    "등등", "기타", "여러", "좋게", "괜찮게", "비슷하게", "같은거", "그런거",
    "이런거", "많이", "빨리", "간단히", "심플하게", "적절히", "쭉", "다들",
    "it", "this", "that", "thing", "things", "stuff", "nice", "good",
    "better", "properly", "somehow", "whatever", "etc", "some",
}

_VAGUE_PHRASES = re.compile(
    r"(이런\s*식으로|저런\s*식으로|그런\s*식으로|알아서\s*해|잘\s*(해|되게|좀)|"
    r"적당(히|한)|대충|느낌으로|비슷한\s*걸로|아무거나|make\s+it\s+nice)",
    re.IGNORECASE,
)

_CS_TERMS = {
    "api", "json", "csv", "yaml", "sql", "http", "https", "url", "endpoint",
    "함수", "변수", "배열", "객체", "리스트", "딕셔너리", "스키마", "쿼리",
    "커밋", "브랜치", "머지", "리베이스", "pr", "diff", "로그",
    "에러", "예외", "타입", "인터페이스", "컴포넌트", "상태", "캐시", "비동기",
    "파싱", "리팩터", "리팩토링", "테스트", "빌드", "배포", "마이그레이션",
    "인덱스", "조인", "트랜잭션", "포트", "환경변수", "의존성", "정규식",
    "파일", "디렉토리", "폴더", "모듈", "패키지", "클래스", "메서드", "필드",
    "파라미터", "인자", "리턴", "반환", "루프", "조건문", "재시도", "타임아웃",
    "인증", "토큰", "헤더", "요청", "응답", "필터", "정렬", "집계", "스크립트",
    "schema", "query", "commit", "branch", "async", "cache",
    "timeout", "retry", "token", "header", "request", "response", "parse",
}

_CONCRETE = [
    re.compile(r"`[^`]+`"),                              # code span
    re.compile(r"\b[\w./~-]+\.(py|ts|tsx|js|jsx|json|md|html|css|scss|sh|"
               r"ya?ml|sql|go|rs|java|toml|txt|csv|jsonl)\b"),
    re.compile(r"(^|\s)(~|\.{1,2})?/[\w./-]+"),          # path
    re.compile(r"\b\w+_\w+\b"),                          # snake_case
    re.compile(r"\b[a-z]+[A-Z]\w*\b"),                   # camelCase
    re.compile(r"\b[A-Z][a-z]+[A-Z]\w*\b"),              # PascalCase
    re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b"),               # CONSTANT
    re.compile(r"\w+\(\)"),                              # call
    re.compile(r"https?://\S+"),                         # url
    re.compile(r"\d+\s*(번|개|초|분|시간|일|줄|자|칸|ms|s|px|%|MB|KB|GB|"
               r"포트|건|회|차|위)"),
    re.compile(r'"[^"]{2,}"|\'[^\']{2,}\''),             # quoted literal
]

_STRUCTURE_LIST = re.compile(r"^\s*([-*•]|\d+[.)]|#{1,6}\s)", re.MULTILINE)
_STRUCTURE_ORDER = re.compile(
    r"(먼저|우선|그\s*다음|다음으로|그리고\s*나서|이후에|마지막으로|끝으로|"
    r"\d\s*단계|first|then|next|finally|after\s+that)",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?]|(다|요|줘|봐|자)\s*(\n|$)")

_CONSTRAINT = re.compile(
    r"(대신에?|말고|하지\s*말고|건드리지\s*말|바꾸지\s*말|수정하지\s*말|제외하고|빼고|"
    r"까지만|이내|이상|이하|최대|최소|반드시|무조건|절대|없이|"
    r"만약|경우에는?|조건|예외|형식으로|포맷으로|단위로|기준으로|유지하고|유지한\s*채|"
    r"그대로\s*두고|only|must|should\s+not|don'?t|do\s+not|instead|except|"
    r"without|at\s+most|at\s+least|keep\s+the)",
    re.IGNORECASE,
)
_OUTPUT_FORMAT = re.compile(
    r"(json|csv|마크다운|markdown|표로|테이블로|리스트로|목록으로|형식으로|포맷으로|"
    r"코드블록|bullet|스키마로)",
    re.IGNORECASE,
)


def _count(patterns, text):
    return sum(len(p.findall(text)) for p in patterns)


def features(text):
    """Raw, uncalibrated signals for one prompt."""
    tokens = tokenize(text)
    n = max(len(tokens), 1)

    vague_terms = sorted({t for t in tokens if t in _VAGUE_TOKENS})
    vague_hits = sum(1 for t in tokens if t in _VAGUE_TOKENS)
    vague_hits += len(_VAGUE_PHRASES.findall(text))

    concrete_hits = _count(_CONCRETE, text)
    cs_hits = sum(1 for t in tokens if t in _CS_TERMS)

    lines = [ln for ln in text.splitlines() if ln.strip()]
    list_items = len(_STRUCTURE_LIST.findall(text))
    order_words = len(_STRUCTURE_ORDER.findall(text))
    sentences = max(len(_SENTENCE_END.findall(text)), 1)

    constraint_hits = len(_CONSTRAINT.findall(text))
    format_hits = len(_OUTPUT_FORMAT.findall(text))

    return {
        "tokens": len(tokens),
        "vague_hits": vague_hits,
        "vague_terms": vague_terms,
        "concrete_hits": concrete_hits,
        "cs_hits": cs_hits,
        "list_items": list_items,
        "order_words": order_words,
        "lines": len(lines),
        "sentences": sentences,
        "constraint_hits": constraint_hits,
        "format_hits": format_hits,
        # Continuous axes, later converted to percentile ranks.
        "specificity_raw": (concrete_hits + 0.6 * cs_hits) / n - 1.4 * vague_hits / n,
        "structure_raw": (2.0 * list_items + 1.5 * order_words
                          + 0.8 * max(len(lines) - 1, 0) + 0.5 * (sentences - 1)),
        "constraint_raw": (1.2 * constraint_hits + 1.0 * format_hits) / (1 + n / 40.0),
    }
