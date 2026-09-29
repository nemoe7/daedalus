"""An example daedalus hook file. It changes nothing.

Copy it, and name the copy in the hooks list of a model, provider or file:

  hooks:
    - on-upstream: hooks/my_hook.py

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
