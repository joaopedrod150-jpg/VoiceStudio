"""Language handling for text sent to the TTS engine.

Two jobs, both model-free and identical on every OS:

* ``detect_language`` - guess the language of text when the request says
  "Auto". Before this, "Auto" meant *no* language reached the engine
  ("language-agnostic mode"), the numbers normaliser skipped the text and
  per-language pronunciation entries never matched. Portuguese with R-heavy
  words and English loan words was the visible symptom.
* ``[lang:xx]...[/lang]`` spans - mark a word or passage with its own
  language (``Eu trabalho de [lang:en]night[/lang]``); each run is rendered
  with its language and the audio is joined.

The detector is a stop-word/diacritic scorer: tiny, offline, and it declines
(returns ``None``) when unsure, so "Auto" keeps its old behaviour for short or
ambiguous text.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

LANG_SPAN_RE = re.compile(r"\[lang:([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?)\](.*?)\[/lang\]", re.DOTALL)

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

_STOP = {
    "pt": "o a os as um uma uns umas de do da dos das em no na nos nas por para com sem que e é são não nao mais muito "
          "mas como se ao aos à às eu você voce ele ela nós nos eles elas meu minha seu sua isso isto esse essa aqui "
          "ali já foi era está estou tem têm vai vou ser ter fazer hoje também tambem quando porque pra tá".split(),
    "es": "el la los las un una unos unas de del en por para con sin que y es son no más muy pero como se al yo usted "
          "él ella nosotros ellos mi tu su esto eso ese esa aquí ya fue era está estoy tiene va voy ser tener hacer hoy "
          "también cuando porque".split(),
    "en": "the a an of to in on at for with without that and is are was were not more very but as it he she we they i "
          "you my your his her this that these those here there have has had do does did will would can could be been "
          "today also when because what from by or if".split(),
    "fr": "le la les un une des de du en pour avec sans que et est sont pas plus très mais comme se au aux je tu il elle "
          "nous vous ils elles mon ma ton son sa ce cette ces ici déjà été était suis a ont va être avoir faire "
          "aujourd'hui aussi quand parce".split(),
    "de": "der die das ein eine einen von zu im in auf mit ohne dass und ist sind war nicht mehr sehr aber wie es er sie "
          "wir ihr ich du mein dein sein dies hier schon wurde habe hat haben wird sein auch wenn weil".split(),
    "it": "il lo la i gli le un una uno di del della in per con senza che e è sono non più molto ma come si al io tu "
          "lui lei noi voi loro mio tuo suo questo quello qui già era sono ha hanno va essere avere fare oggi anche "
          "quando perché".split(),
}
_STOP = {lang: set(words) for lang, words in _STOP.items()}
# Shared words are weak evidence; letters unique to one language are strong.
_MARKERS = {
    "pt": "ãõç",
    "es": "ñ¿¡",
    "fr": "èêëîïôûùœ",
    "de": "ßäöü",
    "it": "ìò",
}
MIN_WORDS = 4


def detect_language(text: str) -> Optional[str]:
    """ISO code of the most likely language, or ``None`` when not confident."""
    if not text:
        return None
    cleaned = LANG_SPAN_RE.sub(" ", text)
    cleaned = re.sub(r"\[[^\]]*\]", " ", cleaned)
    words = [w.lower() for w in _WORD_RE.findall(cleaned)]
    if len(words) < MIN_WORDS:
        return None
    lowered = cleaned.lower()
    scores = {}
    for lang, stop in _STOP.items():
        score = sum(1 for w in words if w in stop)
        score += 3 * sum(lowered.count(ch) for ch in _MARKERS.get(lang, ""))
        scores[lang] = score
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    (best, top), (_, second) = ranked[0], ranked[1]
    if top < 2 or top < second * 1.3 + 0.5:
        return None
    return best


def has_lang_tags(text: str) -> bool:
    return bool(text) and "[lang:" in text and LANG_SPAN_RE.search(text) is not None


def split_lang_runs(text: str, default: Optional[str]) -> List[Tuple[str, Optional[str]]]:
    """Split ``text`` into ``(run_text, language)`` runs; untagged text uses ``default``."""
    if not has_lang_tags(text):
        return [(text, default)]
    runs: List[Tuple[str, Optional[str]]] = []
    last = 0
    for m in LANG_SPAN_RE.finditer(text):
        before = text[last:m.start()]
        if before.strip():
            runs.append((before, default))
        inner = m.group(2)
        if inner.strip():
            runs.append((inner, m.group(1).replace("_", "-")))
        last = m.end()
    tail = text[last:]
    if tail.strip():
        runs.append((tail, default))
    return runs or [("", default)]


def strip_lang_tags(text: str) -> str:
    """Remove the tags, keeping their content (for paths that cannot switch language)."""
    return LANG_SPAN_RE.sub(lambda m: m.group(2), text) if has_lang_tags(text) else text
