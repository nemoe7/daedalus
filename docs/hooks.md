# Hooks

A hook file is Python code that changes the chat requests and answers of 1 provider. The file is `config/hooks/{provider}.py`, for example `config/hooks/openrouter.py`. The provider name is the name of its block in the provider files.

## Hook points

A hook file defines 1 function for each hook point that it uses. Each function is optional.

| Function | When | Gets |
| --- | --- | --- |
| `request(body, model, headers)` | Before each chat request goes to the provider, also for a stream and for each fallback | `body`: the upstream JSON body, after all daedalus changes. For a native API, such as Gemini, the body has the native format. `model`: `provider/slug`. `headers`: the provider headers, as a dict that the hook can change. |
| `answer(answer, model)` | After a full chat answer comes back, before the client gets it | `answer`: the answer in the OpenAI format. A stream has no `answer` hook. |

A function gets a copy of the value. It can change the copy and return None, or it can return a new dict.

## Errors

A hook error does not stop the request:

- An exception, or a return value that is not a dict or None: daedalus logs the error. The value goes on without the hook changes.
- A file that does not load: daedalus writes the error to the log and uses no hook for that provider.

## Changes

daedalus reads the file again when its file time changes. A restart is not necessary.

The dashboard does not edit hook files. Only a person with access to the `config` folder can add one. A hook runs inside the daedalus process, with all its access.

## Example

This hook tells OpenRouter to use only the endpoints in `provider.order`, and to use no other endpoint:

```python
def request(body, model, headers):
  if "order" in body.get("provider", {}):
    body["provider"]["allow_fallbacks"] = False
```

The `cheapest_output` model key makes `provider.order` without a hook. See [Configuration](configuration.md#provider-files).

## New hook points

A new hook point is 1 name in `daedalus/providers/hooks.py` and 1 `hooks.run` call where the value is ready.
