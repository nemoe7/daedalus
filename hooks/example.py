# ---
# version: 1.0.8
# surfaces: []
# author: nemoe7
# title: Example hook
# description: An example hook file that changes nothing.
# license: nemoe7 Noncommercial and Public-Interest License 1.2.0
# ---
"""An example daedalus hook file. It changes nothing.

Copy it, and specify copy in hooks list of a model, provider, or file:

  hooks:
    - on-upstream: hooks/my_hook.py

A request-level surface, such as `on-request`, goes in the `hooks` group of `config/daedalus.yml`:

  hooks:
    on-request: [hooks/my_hook.py]

A `surfaces` list in the block above joins those surfaces with no key there.

Each function is optional. daedalus calls only the functions of surfaces in the list.

A function gets a copy. It can change the copy and return None, or it can return a new dict.
An error goes to the log, and the value goes on without the changes of the hook.
"""


def on_catalog(row: dict, model: str, api_base: str, headers: dict) -> None:
  """At each catalog build, for each model with the hook: the catalog row before the store write.

  The row has the model id and the catalog columns, for example `max_input_tokens`.
  `api_base` and `headers` let the hook call the provider API. A catalog build runs in a worker thread.
  """
  # Example: give 1 model a smaller context window.
  # if model == "openrouter/some/model":
  #   row["max_input_tokens"] = 32768


def on_request(value: dict, model: str, headers: dict) -> None:
  """Before chain of a chat request: request values and client headers.

  `value` holds `key` (None) and `digest` (the messages without the system rows). A `key`
  string counts the requests of that key: a repeat after an answer is a try again.
  """
  # Example: count the requests of each chat of Open WebUI.
  # chat = headers.get("x-openwebui-chat-id")
  # if chat and model == "daedalus/auto":
  #   value["key"] = f"{chat}:{value['digest']}"


def on_prompt(
  value: dict,
  messages: list,
  prompt: str,
  model: str,
  tier: int | None,
  tier_name: str | None,
  slot: str | None,
  reasoning: list,
  effort: str | None,
  body: dict,
  config: dict,
  key: str,
  app: str | None,
) -> None:
  """Before the first attempt of a chat request that has a reasoning model in its chain.

  `value` starts empty. Set `value["reasoning_effort"]` to a string to set the effort. `messages` is
  the request list.

  `prompt` joins user turns. `tier` and `tier_name` identify ladder tier, and
  `slot` identifies pool slot. `reasoning` lists chain models with reasoning support.

  `effort` holds client value, or `None` when absent. A hook value wins.
  `app` identifies client app: `OWUI`, `Kilo`, another title, or `None`.
  """
  # Example: think on a hard prompt only, and keep the rest quick.
  # from daedalus.routing import router
  # if router.required_tier(prompt) == 4 and effort is None:
  #   value["reasoning_effort"] = "high"


def on_http(
  body: dict,
  key: str = "",
  prompt: str = "",
  headers: dict | None = None,
  pin: tuple[str, str] | None = None,
) -> dict:
  """On `POST /v1/hook/<file>`: JSON body of call, and dict to return.

  File sits under `hooks`, identified by relative path. Route passes JSON body,
  chat session `key` (bearer token and first user turn), first user turn as `prompt`,
  and request `headers`. Route also passes `pin`, slot and model of last chat answer,
  when function signature lists `pin`.

  Any valid key may call a hook file, so treat it as admin code. The returned dict is the JSON answer.
  """
  # Example: answer with the model and the time of the call.
  # return {"model": body.get("model"), "at": time.time()}
  return {}


def on_upstream(body: dict, model: str, headers: dict) -> None:
  """Before each chat request goes to provider: upstream body and provider headers."""
  # Example: add a header and a body field.
  # headers["x-example"] = "1"
  # body.setdefault("user", "daedalus")


def on_answer(answer: dict, model: str) -> None:
  """After a full chat answer comes back, before the client gets it. A stream has no on_answer call."""
  # Example: add a mark to the answer text.
  # message = answer["choices"][0]["message"]
  # message["content"] = (message.get("content") or "") + " (via daedalus)"


def on_chunk(chunk: dict, model: str, context: dict) -> None:
  """On each streamed chunk of a chat request, before the client gets it: 1 OpenAI chunk.

  `model` holds requested model ID. `context` holds call metadata, and `previous` holds
  model ID of last session answer (empty on first answer).

  `attempts` counts failures so far. `code` identifies retry code, `pool` identifies landed pool,
  and `served` identifies landed model.
  """
  # Example: mark the last chunk of the answer.
  # if any(choice.get("finish_reason") for choice in chunk.get("choices") or []):
  #   chunk["usage"] = {**(chunk.get("usage") or {}), "example": model}
