# Pronunciation and language (fork notes)

## What was wrong

With the language picker on **Auto** the app sent *no* language to the engine
(`language=None`, "language-agnostic mode"). Consequences, all found by reading
the `/generate` path in `backend/api/routers/generation.py`:

1. OmniVoice had to guess the language per request. Portuguese sentences with
   R sounds and English loan words (`night`) were the failure cases.
2. The numbers/abbreviation normaliser (`services/text_normalization.py`) skipped
   the text, so digits stayed digits.
3. Pronunciation entries scoped to a language (`pt`, `en`) never matched on Auto,
   because the request language was `None`.
4. One request = one language, so a Portuguese sentence with one English word
   could never be rendered correctly.

Not verifiable here: the audio itself (no model weights in the test
environment). The changes below fix the deterministic causes and are covered by
tests; judge the audible improvement on your own engine/voice.

## What changed

* `services/lang_spans.py::detect_language` - offline stop-word/diacritic
  detector (pt, es, en, fr, de, it). It declines when unsure.
* On **Auto**, the detected language is used for normalisation and for
  language-scoped pronunciation entries. It is also sent to engines that accept
  any language (OmniVoice); engines with a fixed language list still receive
  `None`. Disable with `OMNIVOICE_LANG_AUTODETECT=0`.
* **Language per word or passage**: wrap text in `[lang:xx]...[/lang]`, e.g.
  `Eu trabalho de [lang:en]night[/lang] e durmo de dia.` Each run is rendered with
  its own language and the audio is joined (mastering is applied once, after the
  join). Streaming drops the tags and renders one language.
* The existing pronunciation dictionary (Settings -> Pronunciation: word ->
  respelling, optionally per language, applied before generation; `[[...]]`
  inline overrides) already had a UI; it now also fires on Auto.

## Tests

`tests/test_lang_spans.py` (pt-BR, English, mixed, Spanish, short text fixtures)
and `tests/test_generate_language_runs.py` (end-to-end through `/generate`).
