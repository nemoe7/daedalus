# Dashboard

Open `http://HOST:3357/`.

| Login | Value |
| --- | --- |
| User | `admin` |
| Password | `DAEDALUS_MASTER_KEY` |
| Remember me | 30 days. Else the session stops when the browser closes, or after 12 h. |

The dashboard is mainly a utility for monitoring the application state and fine-tuning the settings.

## Pages

| Page | Contents |
| --- | --- |
| Overview | Status, pools and the last requests |
| Pools | The models of each pool, the media pools included, the highest weight first. "+N more" opens the Models page with the filters of the pool. The chip beside the pool name shows the largest context of a pool model. The bar under the pool name shows the mean weight of the pool. A model in a cooldown counts as 0. **Reset weights and cooldowns** sets all weights back to 1 and ends all cooldowns, after a confirmation. The session models stay. |
| Requests | Each request: model, reasoning effort, pool, the model that answered, input tokens, time to first token, stream time, fallbacks and each attempt. A request in flight shows at the top as soon as Daedalus gets it. Its TTFT clock counts until the first token, then its stream clock counts until the last chunk. Without a stream, the stream clock shows the total time. The page gets these rows from a server-sent event stream. The effort column shows the effort from the client. When the model that answered got a different effort, "to" and that effort follow. Each attempt shows the effort that went to its model. "not sent" means that Daedalus deleted the effort for a model that does not reason. The attempt that started a cooldown shows a cooldown mark. Errors have a copy button. The last 500 requests stay in `.daedalus-state/models.sqlite3` after a restart. The page shows 50, and "Show more" adds 50. The input column shows the input token count of the provider. When the provider sends no count, it shows the Daedalus estimate (characters / 4) with a `~` mark. A request that the client closed before the last byte shows "cancelled" in the status column. |
| Models | All catalog models, with a type filter, a tier filter, column sort, a reasoning column, the time left of each cooldown and weight bars. The reasoning column shows the default effort from the catalog as a chip, for example Max. The chip color grows with the effort: gray for None and Minimal, then blue, orange and red. A reasoning model with no default effort shows Yes. The type column shows a chip for the mode and a chip for each media flag: Image in, PDF in, Audio in and Audio out. The type filter also finds models by these chips. With no column chosen, the rows sort by type, then by model name. |
| API keys | Make and delete API keys |
| Providers | A tab for each provider file. The Form view shows 1 card for each provider of the file. The YAML view shows the file text. See [Providers](#providers). |
| Settings | Form and YAML views for `config/daedalus.yml`. The mouse wheel changes the last decimal digit of a decimal field. The YAML view edits the file text, comments included. A save checks each value, then reloads the settings. A change to the other view asks for a confirmation when the file holds unsaved changes. The Escalation and Switch cards take the keywords, 1 on each line. |

The header stays at the top of the window. Only the page below it scrolls. A hidden browser tab sends no requests. It gets new data when it shows again.

The Catalog chip in the header shows the time of the last catalog rebuild and the next scheduled rebuild. Click the chip to rebuild the catalog now, after a confirmation. The chip shows "rebuilding" until the rebuild ends. Only 1 rebuild runs at a time.

The Requests page shows "koinos from moros" when a `daedalus/auto` request starts in moros and a koinos model answers. It shows "try again N" for a [try again](architecture.md#try-again) in Open WebUI.

## Providers

The cards of the Form view stack in columns. Each card goes to the shortest column.

| Field | YAML key | Input |
| --- | --- | --- |
| API key | `api_key` | Text field. `os.environ/NAME` reads an environment variable. |
| API base | `api_base` | Text field. Empty: the gray placeholder shows the default of the provider. |
| API type | `api_type` | Text field, `openai` or `gemini`. Empty: the gray placeholder shows the default of the provider. |
| Discovery URL | `discovery_url` | Text field. Empty: the gray placeholder shows the default of the provider. |
| Discovery match | `discovery_match` | `key = value` chips |
| Exclude | `exclude` | Pattern chips |
| Tiers | `tier.TIER-A` to `tier.TIER-D` | 1 chip list for each tier |
| Model overrides | `models` | 1 row for each pattern, with `key: value` chips |
| Provider values | Catalog columns at the provider level, for example `reasoning_effort` or `rpm` | `key: value` chips. A model override has priority. |

| Action | Result |
| --- | --- |
| × on a chip | Deletes the value |
| **+ Add** | Opens an input. Enter adds the value. Escape stops. |
| **+ key** | Opens a key list and a value input. A model override takes the catalog columns, `pool` and `timeout`. Provider values take only the catalog columns. |
| **+ Pattern** | Adds a pattern row with no overrides |
| Change of a pattern | Renames the pattern. The row keeps its position. |
| **Save** or Ctrl+S | Writes the file, then reloads the configuration |
| **New provider** | Asks for the provider name, then makes `config/providers/{name}.yml` with 1 card |
| **Delete file** | Deletes the `{provider}.yml` file after a confirmation. The main file stays. |

A value of `true` or `false` becomes a boolean. A value with digits only becomes a number. Other values stay text.

A save from the Form view keeps the comments, the key order, the quotes and the one-line maps of the file. It changes only the lines of the values that changed. An empty list or map leaves the file. An empty pattern row stays, because a pattern alone declares a model.

A change to the other view asks for a confirmation when the file holds unsaved changes. The other view shows the saved file.

A save of a file that is not valid YAML, or that has a key 2 times in 1 map, gets an error message. The file does not change. This rule applies to the Providers page and the Settings page.

## API keys

| Rule | Value |
| --- | --- |
| Format | `sk-` and 43 characters |
| Name | 1 to 40 characters, unique |
| Storage | SHA-256 hash only. The dashboard shows the key 1 time. |
| Access | `/v1` only. Not the dashboard. |
| Log | `key=NAME` |

Give each application its own key. Then you can delete 1 key after a leak, and the other applications continue to work.

Note: do not share Daedalus with other people. The provider terms of service can forbid it. Use it for your own work only.
