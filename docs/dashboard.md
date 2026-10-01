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
| Overview | Status, models, the last requests and the limits, in columns of the same width. The Models card shows the model count, then each pool with its mean weight. Each pool row names the model that served the most of the requests the page holds. A pool without those requests names its catalog first model. The Limits card shows the balances, then the 3 rate limits with the least left, for example 0 of 8K tok/min. A long model name ends in an ellipsis. Point to it to see the full name. A phone shows the status chips and the Log out button in a card above the columns. The header then holds the name and the tabs only. |
| Requests | The log of the last requests and the ones in flight. See [Requests](#requests). |
| Models | The catalog table and a card for each pool. See [Models](#models). |
| API keys | Make and delete API keys |
| Providers | A tab for each provider file. The Form view shows 1 card for each provider of the file. The YAML view shows the file text. See [Providers](#providers). |
| Limits | The last rate-limit headers of each model in a table, and a card with the balance of each provider key beside it. See [Limits](#limits). |
| Settings | Form and YAML views for `config/daedalus.yml`. The mouse wheel changes the last decimal digit of a decimal field. The YAML view edits the file text, comments included. A save checks each value, then reloads the settings. A change to the other view asks for a confirmation when the file holds unsaved changes. The Escalation and Switch cards take the keywords, 1 on each line. The Dashboard card sets the theme and the time format. The Pool names card sets the names after `daedalus/`. An empty field uses the built-in name. A phone hides the Ctrl+S hint and tightens the save bar. |

The logo shows in the header, on the login page and as the tab icon. The page has a web app manifest, so a browser can install the dashboard as an app. A browser installs it only over HTTPS or from localhost. The version shows under the name, also on the login page before a login: the image tag, for example v0.2.0, or dev-COMMIT after `install --dev`. `daedalus --version` shows the same value. The header stays at the top of the window. Only the page below it scrolls. A fade at an edge of the tab bar shows the tabs that wait off screen. A hidden browser tab sends no requests. It gets new data when it shows again.

The Catalog chip shows the time of the last catalog rebuild and the next scheduled rebuild. A wide screen puts it in the header, a phone in the status card of the Overview page. Click the chip to rebuild the catalog now, after a confirmation. The chip shows "rebuilding" until the rebuild ends. Only 1 rebuild runs at a time.

## Requests

The page lists the requests in flight at the top, then the last ones, 50 at
a time and 500 at most. A click on a request opens its attempts, and a hover
on a count or name shows the exact value.

## Models

A card for each pool shows above the table, and a click on a card sets the
filters of that pool. The table sorts by column and filters by type and tier.

## Providers

The cards of the Form view stack in columns. Each card goes to the shortest column. A phone hides the Ctrl+S hint and tightens the bar over the file tabs.

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
