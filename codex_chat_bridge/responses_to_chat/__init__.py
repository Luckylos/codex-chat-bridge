from .errors import UnsupportedResponsesInputItemError
from .request import responses_to_chat_request

# Symmetric entrypoint alias — matches chat_to_responses.convert()
convert = responses_to_chat_request

__all__ = [
    "UnsupportedResponsesInputItemError",
    "responses_to_chat_request",
    "convert",
]
