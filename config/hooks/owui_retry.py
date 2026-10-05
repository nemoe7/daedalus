"""The Open WebUI try-again rule, as a request hook of `config/daedalus.yml`.

Open WebUI sends `x-openwebui-chat-id` when `ENABLE_FORWARD_USER_INFO_HEADERS` is true.
A repeat of a message that a model answered is a try again. For `daedalus/auto`, daedalus
steps 1 tier up. For a named pool, daedalus keeps that pool. In both cases the models that
answered that message leave. Then daedalus calls this point again with `count` filled in,
and this file writes the code of the row, `rt1`, `rt2`.

`config/daedalus.yml` names this file in its `request_hooks` group. An empty value turns the rule
off, and a repeated message then takes the usual chain.
"""

HEADER = "x-openwebui-chat-id"


def on_request(value, model, headers):
  """The key of the turn, and on a repeat the code of the row, for any chat model."""
  chat = headers.get(HEADER)
  if not chat:
    return None
  value["key"] = f"{chat}\x00{value['digest']}"
  count = value.get("count")
  if count:
    value["code"] = f"rt{count}"
  return value


def on_init():
  """The legend row of this hook."""
  return [["rtN", "A repeat picked another model, N times"]]
