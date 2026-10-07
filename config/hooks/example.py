# ---
# version: 1.0.0
# ---
"""An example daedalus hook file. It changes nothing.

Copy it, and name the copy in the hooks list of a model, provider or file:

  hooks:
    - on-upstream: hooks/my_hook.py

A request-level point, such as `on-request`, goes in the `request_hooks` group of `config/daedalus.yml`:

  request_hooks:
    on-request: [hooks/my_hook.py]

Each function is optional. daedalus calls only the functions of the points in the list.
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
  """Before the chain of a chat request: the request values and the client headers.

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

  `value` starts empty. Set `value["reasoning_effort"]` to a string to set the effort. `messages` is the request list. `prompt` holds the
  user turns joined. `tier` and `tier_name` name the ladder tier, `slot` the pool slot.
  `reasoning` lists the chain models that support reasoning.
  `effort` is the value of the client, `None` when it sent none. The value that a hook sets wins.
  `app` names the client app of the request: `OWUI`, `Kilo`, another title, or None.
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
  """On `POST /v1/hook/<file>`: the JSON body of the call, and the dict to answer with.

  The file sits under `config/hooks`, and its path names it. The route passes the JSON body,
  the session `key` of the chat (the bearer token and its first user turn), the first user
  turn as `prompt`, and the request `headers`. It also passes `pin`, the slot and the model
  of the last answer of the chat, when the file names that argument. Any valid key may call a
  hook file, so treat it as admin code. The returned dict is the JSON answer.
  """
  # Example: answer with the model and the time of the call.
  # return {"model": body.get("model"), "at": time.time()}
  return {}


def on_upstream(body: dict, model: str, headers: dict) -> None:
  """Before each chat request goes to the provider: the upstream body and the provider headers."""
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

  `model` is the requested name. `context` holds the fields of the call. `previous` is the model
  of the last session answer, and it is empty on the first. `attempts` counts the failures so
  far. `code` names the retry code, `pool` the landed pool and `served` the landed model.
  """
  # Example: mark the last chunk of the answer.
  # if any(choice.get("finish_reason") for choice in chunk.get("choices") or []):
  #   chunk["usage"] = {**(chunk.get("usage") or {}), "example": model}
