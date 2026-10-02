"""The Open WebUI try-again rule, as a request hook of `config/daedalus.yml`.

Open WebUI sends `x-openwebui-chat-id` when `ENABLE_FORWARD_USER_INFO_HEADERS` is true.
A repeat of a message that a model answered is a try again: daedalus steps 1 tier up and
drops the tier A models that answered that message.

`config/daedalus.yml` names this file in its `request_hooks` group. An empty value turns the rule
off, and a repeated message then takes the usual chain.
"""

from daedalus.routing.router import RESERVED_MODEL

HEADER = "x-openwebui-chat-id"


def on_request(value, model, headers):
  """The key of the turn: the chat id and the message digest, for `daedalus/auto` only."""
  chat = headers.get(HEADER)
  if model != RESERVED_MODEL or not chat:
    return None
  value["key"] = f"{chat}\x00{value['digest']}"
  return value
