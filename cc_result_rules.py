"""Portable result identity rules; callers resolve UI defaults and live state."""

from cc_classify import is_single_word


def local_cache_signature(dictionary_version, formatter_version):
    return "|".join(("local-dictionary", dictionary_version, formatter_version))


def provider_cache_signature(provider_id, model, direction, summary_enabled, language,
                             prompt_revision=""):
    """Join caller-resolved fields without repeating coercion or defaults."""
    fields = [
        provider_id,
        model,
        direction,
        "sum1" if summary_enabled else "sum0",
        language,
    ]
    if prompt_revision:
        fields.append(prompt_revision)
    return "|".join(fields)


def history_kind(origin, content_class, text, *, word_test=is_single_word):
    if origin == "ocr":
        return "ocr"
    if content_class == "code":
        return "code"
    if text and word_test(text):
        return "dict"
    return "text"
