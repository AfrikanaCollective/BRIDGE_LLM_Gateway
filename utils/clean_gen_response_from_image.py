"""
Processes the response from LLM to allow to be ingested as JSON
"""
import re
import json
import logging

logger = logging.getLogger(__name__)

def strip_markdown_code_blocks(text: str) -> str:
    """
    Strip markdown code blocks (```json ... ```) from text.
    Returns the raw content, optionally parsed if it's valid JSON.
    """
    if not isinstance(text, str):
        return text

    # Pattern to match ```[language]\n...\n```
    pattern = r'```(?:json|python|javascript|yaml)?\n(.*?)\n```'
    match = re.search(pattern, text, re.DOTALL)

    if match:
        # Extract content from code block
        content = match.group(1)
        logger.debug(f"🔍 Stripped markdown code block, content length: {len(content)}")
        return content

    # If no code block found, return as-is
    return text


def clean_json_string(s):
    """Remove section headers and fix malformed JSON.

    Moved here from app.py (ARCHITECTURE.md §9) — this is ITF/NAR-specific
    response repair, applied to the raw string the gateway returns; the
    gateway itself never interprets response content as domain JSON
    (CLAUDE.md hard rule).
    """

    # Remove section headers like "A: Mother's details", "B: Labour and Birth"
    s = re.sub(r'",\s*"[A-Z]:\s+[^"]*"', '"', s)
    s = re.sub(r'",\s*"[A-Z]:\s+[^"]+"', '"', s)

    # Fix escaped quotes in values
    s = s.replace("\\'", "'")

    return s


def repair_trailing_bare_strings(json_text: str) -> str:
    """
    Repairs LLM-generated JSON where a key's value is followed by
    additional un-keyed ("bare") string literals before the next real
    "key": pair, e.g.:

        "K: Action plan": "step 1", "step 2", "step 3", "Next key": "value"

    All bare strings following a key's value are merged into that
    value (joined by \\n), continuing until either:
      - a string token that is itself followed by a colon (a real key), or
      - the enclosing object/array closes.

    Moved here from app.py (ARCHITECTURE.md §9) — see clean_json_string
    docstring above for why.
    """
    # Tokenize into quoted strings and structural characters.
    token_pattern = re.compile(r'"(?:[^"\\]|\\.)*"|[{}\[\],:]')
    tokens = token_pattern.findall(json_text)

    def is_str(tok: str) -> bool:
        return tok.startswith('"')

    out_tokens = []
    i, n = 0, len(tokens)

    while i < n:
        tok = tokens[i]
        out_tokens.append(tok)

        # Look for a completed "key": "value" pair
        if (
            is_str(tok)
            and i + 2 < n
            and tokens[i + 1] == ":"
            and is_str(tokens[i + 2])
        ):
            out_tokens.append(tokens[i + 1])  # the colon
            merged_value = json.loads(tokens[i + 2])  # unescape the value string

            j = i + 3
            # Keep consuming ", "<bare string>" as long as that string
            # is NOT itself followed by a colon (which would make it a key)
            while (
                j + 1 < n
                and tokens[j] == ","
                and is_str(tokens[j + 1])
                and not (j + 2 < n and tokens[j + 2] == ":")
            ):
                bare_str = json.loads(tokens[j + 1])
                merged_value += "\n" + bare_str
                j += 2  # consumed the comma + the bare string

            out_tokens.append(json.dumps(merged_value))  # re-escape merged value
            i = j
            continue

        i += 1

    return "".join(out_tokens)


def repair_unescaped_quotes(text: str, max_attempts: int = 50) -> str:
    """
    Repair a common LLM JSON bug: a string value/key contains literal,
    unescaped double quotes (e.g. tick-box labels like "1" copied verbatim
    from a source document) which breaks the parser mid-string.

    Strategy: attempt json.loads; on a delimiter error caused by a stray
    quote inside a string span, escape that quote and retry.

    Moved here from app.py (ARCHITECTURE.md §9) — see clean_json_string
    docstring above for why.
    """
    for _ in range(max_attempts):
        try:
            json.loads(text, strict=False)
            return text
        except json.JSONDecodeError as e:
            if "Expecting ',' delimiter" not in e.msg and "Expecting ':' delimiter" not in e.msg:
                raise  # different failure mode -- don't mask it

            search_start = max(0, e.pos - 5)
            snippet = text[search_start:e.pos]
            quote_idx = None
            for i in range(len(snippet) - 1, -1, -1):
                if snippet[i] == '"' and (i == 0 or snippet[i - 1] != '\\'):
                    quote_idx = search_start + i
                    break
            if quote_idx is None:
                raise
            text = text[:quote_idx] + '\\"' + text[quote_idx + 1:]
    raise ValueError("Could not repair JSON after max attempts")
