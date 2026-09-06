"""Lexical diversity metrics (MTLD, HD-D) with a Korean-aware tokenizer.

Thorgeirsson et al. (CHI '26) measured prompt lexical diversity with MTLD and
HD-D, both of which are length-independent and therefore usable on the short
texts that prompts are. Both are reimplemented here rather than pulled from
the `lexical-diversity` package so the tool runs on a stock interpreter.

The tokenizer matters more than the metric for Korean. Korean is
agglutinative, so whitespace tokens inflate the type count ("대시보드를",
"대시보드가", "대시보드는" are three types for one lemma) and thus inflate
diversity. `tokenize` strips the common particle and verb-ending suffixes to
approximate a lemma. If `kiwipiepy` is importable it is used instead, which is
considerably more accurate.
"""

import math
import re
from functools import lru_cache

# Particles (조사) that attach to nouns. Ordered longest-first so that the
# longest match wins.
_JOSA = sorted(
    [
        "에서부터", "으로부터", "이라고는", "에게서", "이라는", "라는", "이라고",
        "라고", "에서는", "에게는", "으로는", "에서도", "에게도", "으로도",
        "까지는", "부터는", "에서", "에게", "한테", "으로", "이나", "라도",
        "조차", "마저", "부터", "까지", "처럼", "같이", "보다", "이란", "란",
        "은", "는", "이", "가", "을", "를", "에", "와", "과", "도", "만",
        "의", "로", "나", "야", "께", "요",
    ],
    key=len,
    reverse=True,
)

# Verb/adjective endings. Prompts are overwhelmingly imperative, so the same
# stem shows up as 해줘 / 해주라 / 해봐 / 했어 / 하자 / 합니다.
_ENDINGS = sorted(
    [
        "해주시겠어요", "해주시겠어", "해주라니까", "해주세요", "해주라", "해줘야",
        "해줄래", "해보자", "해봐야", "해줘", "해봐", "해서", "했었", "했는데",
        "했어", "하자", "하고", "하는", "하면", "해도", "합니다", "했습니다",
        "됐어", "돼야", "되는", "되면", "된다", "이야", "예요", "이에요",
        "거든", "는데", "지만", "으니", "니까", "어야", "아야", "겠다", "겠어",
    ],
    key=len,
    reverse=True,
)

_HANGUL = re.compile(r"[가-힣]")
# Keep identifier-ish tokens whole: paths, dotted names, snake_case, numbers.
_TOKEN = re.compile(r"[A-Za-z0-9_./~\-]+|[가-힣]+")

# URLs, paths and opaque identifiers are not vocabulary. Left alone, every one
# of them is a fresh type and diversity is inflated past the point of being
# able to tell two sessions apart. Collapsing each class to a single type is
# standard preprocessing and makes repeated references read as repetition,
# which is what they are.
_NOISE = [
    (re.compile(r"https?://\S+"), " URLREF "),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f-]{12,}\b", re.I), " IDREF "),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), " IDREF "),
    (re.compile(r"(?<![\w-])(?:~|\.{1,2})?/[\w./-]{3,}"), " PATHREF "),
    (re.compile(r"```.*?```", re.DOTALL), " CODEREF "),
]


def normalize_noise(text):
    for pattern, placeholder in _NOISE:
        text = pattern.sub(placeholder, text)
    return text


def _strip_suffix(token, suffixes, min_stem):
    for suf in suffixes:
        if token.endswith(suf) and len(token) - len(suf) >= min_stem:
            return token[: -len(suf)]
    return token


def _normalize_korean(token):
    """Approximate a lemma by peeling one verb ending and one particle."""
    token = _strip_suffix(token, _ENDINGS, min_stem=1)
    token = _strip_suffix(token, _JOSA, min_stem=2)
    return token


@lru_cache(maxsize=1)
def _kiwi():
    try:
        from kiwipiepy import Kiwi
    except ImportError:
        return None
    return Kiwi()


def tokenize(text, use_morph=True):
    """Split text into lemma-ish tokens suitable for diversity measurement.

    Uses kiwipiepy when it is importable, which lemmatizes properly
    (알려드리면 -> 알리, 써봐 -> 쓰). The regex fallback peels one verb ending
    and one particle, which is coarser but keeps the tool dependency-free.
    """
    text = normalize_noise(text)

    if use_morph:
        kiwi = _kiwi()
        if kiwi is not None:
            # Content morphemes only: nouns, verbs, adjectives, adverbs and
            # foreign words. Numbers and particles carry no vocabulary signal.
            keep = {"NNG", "NNP", "VV", "VA", "MAG", "SL", "XR"}
            return [t.form.lower() for t in kiwi.tokenize(text) if t.tag in keep]

    tokens = []
    for raw in _TOKEN.findall(text):
        if _HANGUL.search(raw):
            raw = _normalize_korean(raw)
        else:
            raw = raw.lower().strip("./-~_")
        if len(raw) >= 1 and not raw.isdigit():
            tokens.append(raw)
    return tokens


def mtld(tokens, threshold=0.72):
    """Measure of Textual Lexical Diversity, bidirectional (McCarthy & Jarvis).

    Returns the mean of the forward and reverse passes. Values below ~50
    tokens are unstable; callers should check `len(tokens)` first.
    """
    if not tokens:
        return 0.0

    def _one_pass(seq):
        factors = 0.0
        types = set()
        count = 0
        ttr = 1.0
        for tok in seq:
            types.add(tok)
            count += 1
            ttr = len(types) / count
            if ttr <= threshold:
                factors += 1
                types, count, ttr = set(), 0, 1.0
        if count > 0:
            # Partial factor for the trailing, unfinished segment.
            denom = 1.0 - threshold
            factors += (1.0 - ttr) / denom if denom else 0.0
        return len(seq) / factors if factors > 0 else float(len(seq))

    return (_one_pass(tokens) + _one_pass(list(reversed(tokens)))) / 2.0


def hdd(tokens, sample_size=42):
    """HD-D: mean contribution to a 42-token sample's type count, scaled to 0-1.

    For each type, the hypergeometric probability of it appearing at least once
    in a random draw of `sample_size` tokens, averaged over the sample size.
    """
    n = len(tokens)
    if n < sample_size:
        return 0.0

    counts = {}
    for tok in tokens:
        counts[tok] = counts.get(tok, 0) + 1

    log_total = _log_comb(n, sample_size)
    total = 0.0
    for freq in counts.values():
        remaining = n - freq
        if remaining < sample_size:
            prob_absent = 0.0
        else:
            prob_absent = math.exp(_log_comb(remaining, sample_size) - log_total)
        total += (1.0 - prob_absent) / sample_size
    return total


def _log_comb(n, k):
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
