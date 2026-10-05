import pytest
from pydantic import ValidationError

from gateway.models.chat import ChatCompletionRequest, Message


def _request(**kwargs) -> ChatCompletionRequest:
    return ChatCompletionRequest(model="m", messages=[Message(role="user", content="hi")], **kwargs)


def test_num_ctx_defaults_to_32k() -> None:
    assert _request().num_ctx == 32768


def test_num_ctx_at_cap_is_accepted() -> None:
    assert _request(num_ctx=32768).num_ctx == 32768


@pytest.mark.parametrize("value", [32769, 65536, 0, -1])
def test_num_ctx_out_of_range_is_rejected(value: int) -> None:
    with pytest.raises(ValidationError):
        _request(num_ctx=value)
