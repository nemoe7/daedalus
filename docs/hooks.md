# Hooks

A hook is Python code that changes the chat requests and answers of 1 provider. The `hooks` list of a provider block names the hook files:

```yaml
openrouter:
  hooks:
    - on-upstream: hooks/openrouter_only.py
    - on-answer: hooks/answer.py
```

Each item has 1 hook point and 1 file path. The path starts in the `config` folder, and the file must stay in that folder. The file name can be any name.

## Hook points

For each point, the file defines 1 function with the name of the point.

| Point | Function | When | Gets |
| --- | --- | --- | --- |
| `on-upstream` | `on_upstream(body, model, headers)` | Before each chat request goes to the provider, also for a stream and for each fallback | `body`: the upstream JSON body, after all daedalus changes. For a native API, such as Gemini, the body has the native format. `model`: `provider/slug`. `headers`: the provider headers, as a dict that the hook can change. |
| `on-answer` | `on_answer(answer, model)` | After a full chat answer comes back, before the client gets it | `answer`: the answer in the OpenAI format. A stream has no `on-answer` point. |

A function gets a copy of the value. It can change the copy and return None, or it can return a new dict. The hooks of 1 point run in list order, and each hook gets the value of the hook before it.

## Errors

A hook error does not stop the request. daedalus logs the error, and the value goes on without the changes of that hook. The next hook in the list still runs. These are errors:

- An exception, or a return value that is not a dict or None
- A file that is not there, does not load, or has no function for the point
- A path outside the `config` folder, or an unknown point

## Changes

daedalus reads a file again when its file time changes. A restart is not necessary.

The dashboard can edit the `hooks` list, but not the hook files. Only a person with access to the `config` folder can add a file. A hook runs inside the daedalus process, with all its access.

## Example

This hook tells OpenRouter to use only the endpoints in `provider.order`, and to use no other endpoint:

```python
def on_upstream(body, model, headers):
  if "order" in body.get("provider", {}):
    body["provider"]["allow_fallbacks"] = False
```

The `cheapest_output` model key makes `provider.order` without a hook. See [Configuration](configuration.md#provider-files).

## New hook points

A new hook point is 1 item in `POINTS` in `daedalus/providers/hooks.py`, and 1 `hooks.run` call where the value is ready.
