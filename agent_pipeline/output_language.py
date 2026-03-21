from __future__ import annotations

SUPPORTED_OUTPUT_LANGUAGES = ("en", "zh")

_LANGUAGE_ALIASES = {
    "en": "en",
    "english": "en",
    "en-us": "en",
    "en_us": "en",
    "en-gb": "en",
    "en_gb": "en",
    "英文": "en",
    "zh": "zh",
    "zh-cn": "zh",
    "zh_cn": "zh",
    "cn": "zh",
    "chinese": "zh",
    "中文": "zh",
}


def normalize_output_language(value: object, default: str = "en") -> str:
    fallback = _LANGUAGE_ALIASES.get(str(default).strip().lower(), "en")
    if value is None:
        return fallback

    text = str(value).strip().lower()
    if not text:
        return fallback

    if text in SUPPORTED_OUTPUT_LANGUAGES:
        return text
    return _LANGUAGE_ALIASES.get(text, fallback)


def output_language_name(value: object) -> str:
    return "Chinese" if normalize_output_language(value) == "zh" else "English"


def output_language_label(value: object) -> str:
    return "中文" if normalize_output_language(value) == "zh" else "英文"
