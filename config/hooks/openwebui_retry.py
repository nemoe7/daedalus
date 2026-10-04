"""The Open WebUI try-again rule, as a request hook of `config/daedalus.yml`.

Open WebUI sends `x-openwebui-chat-id` when `ENABLE_FORWARD_USER_INFO_HEADERS` is true.
A repeat of a message that a model answered is a try again. For `daedalus/auto`, daedalus
steps 1 tier up and drops the models that answered that message. Then daedalus calls this
point again with `count` filled in, and this file writes the code of the row, `rt1`, `rt2`.

`config/daedalus.yml` names this file in its `request_hooks` group. An empty value turns the rule
off, and a repeated message then takes the usual chain.
"""

from daedalus.routing.router import RESERVED_MODEL

HEADER = "x-openwebui-chat-id"


def on_request(value, model, headers):
  """The key of the turn, and on a repeat the code of the row, for `daedalus/auto` only."""
  chat = headers.get(HEADER)
  if model != RESERVED_MODEL or not chat:
    return None
  value["key"] = f"{chat}\x00{value['digest']}"
  count = value.get("count")
  if count:
    value["code"] = f"rt{count}"
  return value
