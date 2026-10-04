# Hooks

A hook is Python code that changes the catalog rows, chat requests and answers of a provider or a model. A `hooks` list names the hook files:

```yaml
openrouter:
  hooks:
    - on-upstream: hooks/openrouter_only.py
  models:
    z-ai/glm-5.3-flash:
      hooks:
        - on-catalog: hooks/cheapest_output.py
        - on-upstream: hooks/cheapest_output.py
```

The `hooks` key works like `order`. A `models` entry has priority. Then comes the provider block in the `{provider}.yml` file of the model, then the provider block in the main file. A model with `hooks: []` uses no hooks.

Each item has 1 hook point and 1 file path. The path starts in the `config` folder, and the file must stay in that folder. The file name can be any name.

## Hook points

For each point, the file defines 1 function with the name of the point.

| Point | Function | When | Gets |
| --- | --- | --- | --- |
| `on-request` | `on_request(value, model, headers)` | Before the chain of a chat request, and again on a repeat with the count | `value`: `key` (`None`), `digest`, the hash of the messages without the system rows, and on the second run `count`. `model`: the requested model. `headers`: the client headers. |
| `on-catalog` | `on_catalog(row, model, api_base, headers)` | At each catalog build, for each model with the hook, before the store write | `row`: a catalog row, the id cannot change. `api_base`, `headers`: for the provider API calls. |
| `on-upstream` | `on_upstream(body, model, headers)` | Before each chat request to the provider, streams and fallbacks included | `body`: the upstream JSON body, native format for native APIs. `model`: `provider/slug`. `headers`: changeable. |
| `on-answer` | `on_answer(answer, model)` | After a full chat answer comes back, before the client gets it | `answer`: the answer in the OpenAI format. A stream has no `on-answer` point. |

A request-level point, such as `on-request`, takes its file from the `request_hooks` group of `config/daedalus.yml`, because no provider owns the request yet:

```yaml
request_hooks:
  on-request: hooks/openwebui_retry.py
```

An empty value turns that point off. A file that sets `value["key"]` counts the requests of that key: a repeat after an answer is a try again. `daedalus` then steps the tier 1 step up, and drops the models that answered the message. The point runs again with `count` filled in. A file that writes `value["code"]` sets the code of the Requests row, such as `rt1`. Without a `key`, a repeat is a new request. Without a `code`, the row shows no code. `config/hooks/openwebui_retry.py` applies this rule to the `x-openwebui-chat-id` header of Open WebUI.

A function gets a copy of the value. It can change the copy and return None, or it can return a new dict. The hooks of 1 point run in list order, and each hook gets the value of the hook before it.

## Errors

A hook error does not stop the request. daedalus logs the error, and the value goes on without the changes of that hook. The next hook in the list still runs. These are errors:

- An exception, or a return value that is not a dict or None
- A file that is not there, does not load, or has no function for the point
- A path outside the `config` folder, or an unknown point

## Changes

daedalus reads a file again when its file time changes. A restart is not necessary.

Hooks get no database access. An `on-catalog` hook changes only the row. daedalus writes only the known columns of the row, so a hook cannot change a table. A hook that keeps data uses its own file, such as `cheapest_output.json`.

The dashboard can edit the `hooks` list, but not the hook files. Only a person with access to the `config` folder can add a file. A hook runs inside the daedalus process, with all its access.

## Hook files in config

| File | Use |
| --- | --- |
| `hooks/example.py` | A start for a new hook file: each function, examples in comments, no changes. |
| `hooks/cheapest_output.py` | Sorts the OpenRouter endpoints by the cheapest output price. The example follows the table. |
| `hooks/openwebui_retry.py` | The request hook of Open WebUI: a repeated message with the same chat id is a try again. |

The `cheapest_output` hook: `on_catalog` reads the endpoint list of each model at each
catalog build. It sorts the list by the output price after the discount, and the input
price breaks a tie. The order holds provider slugs with no variant, such as `deepinfra`
for `deepinfra/fp4`, and each provider keeps the place of its cheapest endpoint. The
order stays in `.daedalus-state/cheapest_output.json`. `on_upstream` sends the order as
`provider.order`, and a client `provider` object has priority. If the list read fails,
the old order stays. `openrouter.yml` names the file for `z-ai/glm-5.3-flash`.

## Example

This hook tells OpenRouter to use only the endpoints in `provider.order`, and to use no other endpoint:

```python
def on_upstream(body, model, headers):
  if "order" in body.get("provider", {}):
    body["provider"]["allow_fallbacks"] = False
```

## New hook points

A model point is 1 item in `POINTS` in `daedalus/providers/hooks.py`, and 1 `hooks.run` call where the value is ready. A request point is also 1 key in the `request_hooks` group of the settings, and 1 `hooks.run_request` call.
