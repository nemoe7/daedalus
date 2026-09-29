# Dashboard

Open `http://HOST:3357/`.

| Login | Value |
| --- | --- |
| User | `DAEDALUS_USERNAME`, else `admin`. The form fills in `admin` only when `DAEDALUS_USERNAME` is not set. |
| Password | `DAEDALUS_PASSWORD`, else `DAEDALUS_MASTER_KEY`. Without `DAEDALUS_PASSWORD`, the form shows a master key hint. The eye button shows the password. |
| Remember me | On at the start. 30 days, shown on hover. Else the session stops when the browser closes, or after 12 h. |
| Change | A new master key, user or password stops all sessions. |

The dashboard is mainly a utility for monitoring the application state and fine-tuning the settings.

## Pages

| Page | Contents |
| --- | --- |
| Overview | Status, models, the last requests and the limits, in columns of the same width. The Models card shows the model count, then each pool with its mean weight and the model of the highest weight. The Limits card shows the balances, then the 3 rate limits with the least left, for example 0 of 8K tok/min. A long model name ends in an ellipsis. Point to it to see the full name. |
| Requests | Each request: app, model, reasoning effort, pool, the model that answered, input and output tokens, time to first token, stream time, fallbacks and each attempt. The app column shows a short name from the client headers: Kilo for a Kilo title, referer or user agent, and OWUI for an `X-OpenWebUI-*` header. Another app shows the first 24 characters of its `X-OpenRouter-Title` or `X-Title`. After the end of a request, hover the name to see the API key name. A request in flight shows at the top as soon as daedalus gets it. Until a model answers, it shows the model of the current attempt and the fallback count. Its TTFT clock counts until the first token, then its stream clock counts until the last chunk. Without a stream, the stream clock shows the total time. The page gets these rows from a server-sent event stream. The effort column shows the effort from the client. When the model that answered got a different effort, that effort follows in smaller text. A native value of the same level, for example `thinkingLevel=low` for `low`, shows as the level, also in the fallback chain. A click on a request opens its attempts. Only a request with a fallback or a failed attempt has this list. Each attempt shows the effort that went to its model. "dropped" means that daedalus deleted the effort for a model that does not reason. The attempt that started a cooldown shows a cooldown mark. Errors have a copy button. The last 500 requests stay in `.daedalus-state/models.sqlite3` after a restart. The page shows 50, and "Show more" adds 50. The input column shows the input token count of the provider. When the provider sends no count, it shows the daedalus estimate (characters / 4) with a `~` mark. The output column shows the output token count of the provider. With no count, it shows `-`. From 1,000, both columns show whole thousands, rounded down, for example 79K. Hover a count to see the exact number. A request that the client closed before the last byte shows a gray square in the status column. Hover it to see "Cancelled". A long model name ends in an ellipsis. Point to it to see the full name. |
| Models | The Models tab shows the model count. A card for each pool, the media pools included, shows above the table. The chip beside the pool name shows the largest context of a pool model. The bar shows the mean weight of the pool. A model in a cooldown counts as 0. A click on a pool card sets the tier and type filters of the pool. A second click clears them. **Reset weights and cooldowns** sets all weights back to 1 and ends all cooldowns, after a confirmation. The session models stay. The table shows all catalog models, with a type filter, a tier filter and column sort. It also shows a reasoning column, the time left of each cooldown and weight bars. A client with its own provider key shows its own cooldown under the client name. The order column shows the [order](architecture.md#order) of each model. Order 1 is gray. The reasoning column shows the default effort from the catalog as a chip, for example Max. The chip color grows with the effort: gray for None and Minimal, then blue, orange and red. A reasoning model with no default effort shows Yes. The type column shows a chip for the mode and a chip for each media flag: Image in, PDF in, Audio in and Audio out. The type filter also finds models by these chips. With no column chosen, the rows sort by type, then by model name. |
| API keys | Make and delete API keys |
| Providers | A tab for each provider file. The Form view shows 1 card for each provider of the file. The YAML view shows the file text. See [Providers](#providers). |
| Limits | The last rate-limit headers of each model in a table, and a card with the balance of each provider key beside it. See [Limits](#limits). |
| Settings | Form and YAML views for `config/daedalus.yml`. The mouse wheel changes the last decimal digit of a decimal field. The YAML view edits the file text, comments included. A save checks each value, then reloads the settings. A change to the other view asks for a confirmation when the file holds unsaved changes. The Escalation and Switch cards take the keywords, 1 on each line. The Dashboard card sets the theme and the time format. The Pool names card sets the names after `daedalus/`. An empty field uses the built-in name. |

The logo shows in the header, on the login page and as the tab icon. The page has a web app manifest, so a browser can install the dashboard as an app. A browser installs it only over HTTPS or from localhost. The version shows under the name, also on the login page before a login: the image tag, for example v0.2.0, or dev-COMMIT after `install --dev`. `daedalus --version` shows the same value. The header stays at the top of the window. Only the page below it scrolls. A hidden browser tab sends no requests. It gets new data when it shows again.

The Catalog chip in the header shows the time of the last catalog rebuild and the next scheduled rebuild. Click the chip to rebuild the catalog now, after a confirmation. The chip shows "rebuilding" until the rebuild ends. Only 1 rebuild runs at a time.

When a `daedalus/auto` request goes up 1 or more tiers, the Requests page shows the pool that answers, then "from" and the start pool. It shows "tool loop N" for a [tool loop](architecture.md#loops). It shows "try again N" for a [try again](architecture.md#try-again) in Open WebUI.

## Providers

The cards of the Form view stack in columns. Each card goes to the shortest column.

| Field | YAML key | Input |
| --- | --- | --- |
| API key | `api_key` | `env:NAME` reads the environment. Paste a key to save it in **Keys and values** as `db:PROVIDER_API_KEY`. |
| Account ID | `account_id` | Cloudflare only. `env:NAME` reads the environment. Paste an ID to save it as `db:CLOUDFLARE_ACCOUNT_ID`. |
| Client keys | `client_keys` | `name = value` chips. Use `env:NAME` for an environment value. Pasted keys use `db:PROVIDER_API_KEY_CLIENT`. |
| API base | `api_base` | Text field. Empty: the gray placeholder shows the default of the provider. |
| API type | `api_type` | Text field, `openai` or `gemini`. Empty: the gray placeholder shows the default of the provider. |
| Discovery URL | `discovery_url` | Text field. Empty: the gray placeholder shows the default of the provider. |
| Discovery match | `discovery_match` | `key = value` chips |
| Exclude | `exclude` | Pattern chips |
| Tiers | `tier.TIER-A` to `tier.TIER-D` | 1 chip list for each tier |
| Model overrides | `models` | 1 row for each pattern, with `key: value` chips |
| Provider values | Catalog columns at the provider level, for example `reasoning_effort` or `rpm` | `key: value` chips. A model override has priority. |

### Keys and values

The panel under the form lists the names used by `env:NAME` and `db:NAME` values in provider files and provider defaults.

| Item | Value |
| --- | --- |
| `env:NAME` | Reads the environment variable. |
| `db:NAME` | Reads the saved value. |
| Storage | The `saved_env` table of `.daedalus-state/models.sqlite3`. Not in the YAML or git. |
| Shown | The state: saved, from the environment, or missing. A saved value of 12 or more characters shows its last 4 characters. |
| Save | Takes effect at once, with no restart. A new provider key needs a catalog rebuild. |
| Clear | Deletes the saved value. A `db:NAME` value then resolves empty. `env:NAME` still reads the environment. |

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

A provider file on disk that is not valid YAML still gets its tab. It opens in the YAML view, with the error line next to the Save button. Fix the text there and save it.

## Limits

daedalus reads the balances when it starts, then each hour. **Check now** reads them at once. A provider without a key or without data does not show.

| Provider | Values | Source |
| --- | --- | --- |
| OpenRouter | Credit left of the key limit, credit used today, free requests left today | `GET /api/v1/key` |
| Kilo | Account balance | `GET https://api.kilo.ai/api/profile/balance` |
| Pollinations | Pollen left. A key without a budget needs the `account:usage` scope. | `GET /account/balance` |
| Cloudflare | Neurons used since 00:00 UTC, of the free 10,000. With no use today, it shows 0. | GraphQL `aiInferenceAdaptiveGroups` |
| Groq, Mistral | The limit and the count left of each window, for each model, and the reset time | The `x-ratelimit-*` headers of the last answer |
| Kilo, and each provider with `hourly_requests` | In the card of the provider: the requests left in the last hour, marked counted | The daedalus count. A 429 from the provider sets it to 0. |

The header rows stay in memory only, so they are empty after a restart until the next answer. A client with its own provider key has its own rows. A bar like the weight bar shows the rest of each limit: header rows, OpenRouter credit and free requests, and Cloudflare neurons. A count of 0 shows in red. From 1,000, a count shows floored to K, M or B, for example 998M of 1B. Hover a header row to see the exact number. Each balance comes from the main key of the provider, not from `client_keys`.

To show the Cloudflare neurons, give the Cloudflare token the analytics permission:

1. In the Cloudflare dashboard, open **Manage account → Account API tokens**. For a user token, open **My Profile → API Tokens**.
2. On the token in `CLOUDFLARE_API_KEY`, select **Edit**.
3. Add a permission row: **Account**, **Account Analytics**, **Read**.
4. Select **Continue to summary**, then **Update token**. The token value does not change.

To test the token, send the query of `daedalus/routing/limits.py` with curl. Without the permission, the answer has an `errors` list.

## API keys

| Rule | Value |
| --- | --- |
| Format | `sk-` and 43 characters |
| Name | 1 to 40 characters, unique |
| Storage | SHA-256 hash only. The dashboard shows the key 1 time. |
| Access | `/v1` only. Not the dashboard. |
| Log | `key=NAME` |

Give each application its own key. Then you can delete 1 key after a leak, and the other applications continue to work.

Note: do not share daedalus with other people. The provider terms of service can forbid it. Use it for your own work only.
