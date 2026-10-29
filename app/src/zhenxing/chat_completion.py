"""Small, fail-closed acceptance boundary for complete JSON-chat responses.

This validates the transport envelope, not the application JSON schema or an
execution permission. The caller must still validate the content and source.
Missing finish reasons are accepted only in the explicitly non-strict legacy
mode; production semantic interpretation must pass ``require_finish=True``.
No provider response values are included in errors.
"""


def validated_chat_choice(body, *, require_finish: bool = False) -> dict:
    """Return the only completed assistant text choice or raise a stable code.

    This project's JSON-chat protocol does not accept model tool calls. Null
    optional call fields denote no call; every non-null value is rejected,
    including an empty list or object. Model identity and content-schema checks
    remain the responsibility of the selected provider and the caller.
    """
    if not isinstance(body, dict):
        raise ValueError("MODEL_RESPONSE_INVALID")
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("MODEL_RESPONSE_CHOICES_INVALID")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ValueError("MODEL_RESPONSE_CHOICE_INVALID")
    if "index" in choice and (type(choice["index"]) is not int or choice["index"] != 0):
        raise ValueError("MODEL_RESPONSE_INDEX_INVALID")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("MODEL_RESPONSE_MESSAGE_INVALID")
    if "role" in message and message["role"] != "assistant":
        raise ValueError("MODEL_RESPONSE_ROLE_INVALID")
    if message.get("tool_calls") is not None or message.get("function_call") is not None:
        raise ValueError("MODEL_RESPONSE_TOOL_CALL_UNEXPECTED")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("MODEL_RESPONSE_CONTENT_INVALID")
    reason = choice.get("finish_reason")
    if reason != "stop" and (require_finish or reason is not None):
        raise ValueError("MODEL_RESPONSE_FINISH_REASON_INVALID")
    return choice
