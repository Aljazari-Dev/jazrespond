from __future__ import annotations

from typing import Any, Dict

LANGUAGE_RULES = {
    "ar": (
        "CURRENT LANGUAGE MODE: Arabic. Answer in natural clear Arabic. "
        "Iraqi Arabic is preferred when appropriate. Do not switch to English "
        "unless the user explicitly asks."
    ),
    "en": (
        "CURRENT LANGUAGE MODE: English. Answer only in natural concise English "
        "unless the user explicitly asks for another language."
    ),
    "ku": (
        "CURRENT LANGUAGE MODE: Sorani Kurdish (Central Kurdish), Hawler/Erbil style. "
        "Answer only in Sorani Kurdish. Do not answer in Kurmanji, Arabic, or English "
        "unless explicitly asked. Use short natural spoken sentences suitable for a robot."
    ),
}


def _extract_language_section(knowledge_base: str, language: str) -> str:
    text = (knowledge_base or "").strip()
    if not text:
        return ""
    markers = {
        "en": ["## English", "# English"],
        "ar": ["## Arabic", "# Arabic"],
        "ku": ["## Sorani Kurdish Only", "## Kurdish", "# Sorani Kurdish", "# Kurdish"],
    }.get(language, [])
    lower = text.lower()
    for marker in markers:
        pos = lower.find(marker.lower())
        if pos < 0:
            continue
        next_pos = len(text)
        for candidate in ["\n## ", "\n# "]:
            found = text.find(candidate, pos + len(marker))
            if found >= 0:
                next_pos = min(next_pos, found)
        section = text[pos:next_pos].strip()
        if section:
            return section
    return text


def build_system_instruction(config: Dict[str, Any], language: str) -> str:
    language = language if language in LANGUAGE_RULES else "ar"
    base_prompt = (config.get("system_prompt") or "").strip()
    quick_prompt = (config.get("quick_answer_prompt") or "").strip()
    detail_prompt = (config.get("detail_answer_prompt") or "").strip()
    knowledge_base = _extract_language_section(config.get("knowledge_base") or "", language)

    routing_rule = (
        "PROMOBOT COMMAND ROUTING RULE: For every ordinary spoken user turn, BEFORE "
        "producing any spoken answer, call the blocking function route_promobot_utterance "
        "with the user's utterance. Wait for its result. If matched=false, answer normally. "
        "If matched=true, the physical robot action is executed externally. If speak_reply=true, "
        "speak exactly reply_text and nothing else. If speak_reply=false, stay silent after the "
        "matched tool result. Never call this routing function for ROBOT_CONTROL_EVENT messages."
    )

    chunks = [
        base_prompt,
        LANGUAGE_RULES[language],
        routing_rule,
        "Keep responses optimized for real-time spoken conversation.",
    ]
    if quick_prompt:
        chunks.append("Dashboard quick-answer guidance:\n" + quick_prompt)
    if detail_prompt:
        chunks.append("Dashboard detailed-answer guidance:\n" + detail_prompt)
    chunks.append(
        "In Live mode there is one natural spoken response per normal question. "
        "Use the quick-answer and detailed-answer guidance to decide the right amount of detail."
    )
    if knowledge_base:
        chunks.append("Knowledge base:\n" + knowledge_base)
    return "\n\n".join(x for x in chunks if x)
