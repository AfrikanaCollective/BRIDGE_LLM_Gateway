"""Tests for utils/clean_gen_response_from_image.py — the legacy
/generate-with-image JSON repair applied to the gateway's raw output
(ARCHITECTURE.md §9). Not gateway code, so not under tests/gateway/.

Regression focus: repair_trailing_bare_strings used to drop every
non-string scalar (numbers, true/false/null) because its tokenizer had no
branch for them, so any model output containing one failed to parse.
"""

import json

import pytest

from utils.clean_gen_response_from_image import (
    repair_trailing_bare_strings,
    repair_unescaped_quotes,
    strip_markdown_code_blocks,
)


def legacy_pipeline(raw: str):
    """Same sequence app.py's generate_with_image runs before json.loads."""
    content = strip_markdown_code_blocks(raw.strip())
    content = repair_trailing_bare_strings(content)
    content = repair_unescaped_quotes(content)
    return json.loads(content, strict=False)


@pytest.mark.parametrize(
    "raw",
    [
        '{"ok": true}',
        '{"ok": false}',
        '{"missing": null}',
        '{"n": 5}',
        '{"n": -3.25e2}',
        '{"weight_kg": 3.4, "apgar": [8, 9], "alive": true, "notes": null, "name": "x"}',
        '{"nested": {"a": 1, "b": [true, null, "s"]}}',
        '[1, 2, 3]',
    ],
)
def test_valid_json_with_non_string_scalars_round_trips_unchanged(raw):
    assert json.loads(repair_trailing_bare_strings(raw)) == json.loads(raw)


def test_bare_strings_still_merged_alongside_scalars():
    raw = '{"Plan": "step 1", "step 2", "step 3", "count": 3, "done": false}'
    assert json.loads(repair_trailing_bare_strings(raw)) == {
        "Plan": "step 1\nstep 2\nstep 3",
        "count": 3,
        "done": False,
    }


def test_bare_strings_merged_at_end_of_object():
    raw = '{"n": 1, "Plan": "a", "b"}'
    assert json.loads(repair_trailing_bare_strings(raw)) == {"n": 1, "Plan": "a\nb"}


def test_string_values_that_look_like_scalars_stay_strings():
    raw = '{"a": "5", "b": "true", "c": "null"}'
    assert json.loads(repair_trailing_bare_strings(raw)) == {"a": "5", "b": "true", "c": "null"}


def test_full_pipeline_fenced_output_with_scalars():
    raw = '```json\n{"baby_sex": "F", "weight_kg": 3.1, "resuscitated": false, "ref": null}\n```'
    assert legacy_pipeline(raw) == {
        "baby_sex": "F",
        "weight_kg": 3.1,
        "resuscitated": False,
        "ref": None,
    }


def test_full_pipeline_unescaped_quotes_keep_their_content():
    # Tick-box label copied verbatim with literal quotes. Previously the
    # bare `1` between the quotes was dropped; it must now survive and be
    # escaped by repair_unescaped_quotes.
    raw = '{"label": "tick "1" box", "score": 2}'
    assert legacy_pipeline(raw) == {"label": 'tick "1" box', "score": 2}
