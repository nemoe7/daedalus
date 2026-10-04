# Dashboard

Open `http://HOST:3357/`.

| Login | Value |
| --- | --- |
| User | `DAEDALUS_USERNAME`, else `admin`. The form fills in `admin` only when `DAEDALUS_USERNAME` is not set. |
| Password | Reads `DAEDALUS_PASSWORD`, else `DAEDALUS_MASTER_KEY`. Without a password, the form shows a master key hint. |
| Remember me | On at the start: 30 days. Else the session ends after 12 h. |
| Change | A new master key, user or password stops all sessions. |

## Pages

| Page | Contents |
| --- | --- |
| Overview | Status, models, last requests and limits in columns. Each pool row names its top model. The page shows state only: the Providers, API keys and Settings pages hold the configuration. |
| Requests | The log of the last requests and the ones in flight. See [Requests](#requests). |
| Models | The catalog table and a card for each pool. See [Models](#models). |
| API keys | Make and delete API keys |
| Providers | A tab per provider file. Form: a card per provider. YAML: the file text. |
| Limits | The last rate-limit headers of each model, and each provider key balance in a card. |
| Settings | Form and YAML views of `config/daedalus.yml`. A save checks each value, then reloads the settings. |

The logo shows in the header, on the login page and as the tab icon. The page has a web app manifest, so a browser can install the dashboard as an app. A browser installs it only over HTTPS or from localhost. The version shows under the name, also on the login page before a login: the image tag, for example v0.2.0, or dev-COMMIT after `install --dev`.

`daedalus --version` shows the same value. The header stays at the top of the window. Only the page below it scrolls. A fade at an edge of the tab bar shows the tabs that wait off screen.

A hidden browser tab sends no requests. It gets new data when it shows again.

The header shows the state of daedalus as text. The text uses the style of the page switcher: a health dot, then the sessions, then the catalog line. The line gives the time of the last catalog rebuild. It also gives the next scheduled rebuild.

A phone shows a status card at the top of the Overview page instead: the health line, then 1
line for the sessions and 1 line for the catalog, each with its value at the right. The header

keeps its Log out button on a phone. Log out asks in a modal. Click the catalog line to rebuild the catalog now, after a confirmation. The line shows "rebuilding" until the rebuild ends.

Only 1 rebuild runs at a time. A confirmation of a dangerous action uses a modal of the dashboard, not the browser box.

## Demo on GitHub Pages

A static demo of this page runs at <https://nemoe7.github.io/daedalus/>. Set the Pages source of the repository to **GitHub Actions** 1 time, and the Pages demo workflow deploys each change to the page. No server runs there. `scripts/pages_demo.py` copies the page and adds `demo.js`.

That script answers each `ui/api/` call from `scripts/pages_fixtures.json`. The Requests tab starts empty, and the script streams the captured requests in, then keeps the table rotating. A save lands in the page memory, and it takes the checks of the server. Those checks cover the YAML of a file, the block of a provider name, and the rules of a key name.

A reload brings the captured data back. The Catalog chip starts a rebuild in the page, and it finishes after about 26 seconds. The header shows the version `demo`.

The Pages demo workflow builds the demo from `main` and deploys it after a change to the page, the script or the fixtures. After a change to an endpoint, run `python3 scripts/pages_demo.py --capture` and commit the new fixtures.

## Requests

Each model name reads as the model id does: the mark of the provider, then the mark of the
developer when the developer is another name, then the model part, such as `clef`, with a `/`
between the parts. A name with a shipped SVG file under `daedalus/dashboard/ui/icons/` shows that
mark, and a name with no file shows its own text. The folder holds the marks of
`lobehub/lobe-icons` (MIT), and 1 new file with the name in the model id adds a mark. The hover
text and the cell title keep the full `provider/slug`.

The page lists the requests in flight at the top, then the last ones, 50 at
a time and 500 at most. A click on a request opens its attempts, and a hover
on a count or name shows the exact value. A request that the client closed shows the code `499`.
The Pool column shows 3-character
codes, such as `lmt`, `rt1`, `frX` and `tlN`, and the Code legend card beside the
table gives the meaning of each code. A hook file that defines `on_init` adds
its rows below the base rows of that card. A wide screen keeps the table in its
own panel, so the head stays in view and the panel scrolls sideways: the page
never scrolls sideways. A phone shows the page scroll and its card list.

## Models

A card for each pool shows above the table, and a click on a card sets the
filters of that pool. The table sorts by column and filters by type and tier.

## Providers

The cards of the Form view stack in columns. A phone hides the Ctrl+S hint and tightens the bar over the file tabs.

| Field | YAML key | Input |
| --- | --- | --- |
| API key | `api_key` | `env:NAME` reads the environment. The page saves a pasted key as `db:PROVIDER_API_KEY`. |
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
| Shown | Saved, from the environment, or missing. A long saved value shows its last 4 characters. |
| Save | Takes effect at once, with no restart. A new provider key needs a catalog rebuild. |
| Clear | Deletes the saved value. A `db:NAME` value then resolves empty. `env:NAME` still reads the environment. |

| Action | Result |
| --- | --- |
| × on a chip | Deletes the value |
| **+ Add** | Opens an input. Enter adds the value. Escape stops. |
| **+ key** | A key list and a value input. Model overrides also take `pool` and `timeout`. |
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
| Cloudflare | Neurons left since 00:00 UTC, of the free 10,000. | GraphQL `aiInferenceAdaptiveGroups` |
| Groq, Mistral | The limit, the count left and the reset time of each window, for each model | The `x-ratelimit-*` headers of the last answer |
| Kilo, and each provider with `hourly_requests` | In the card of the provider: the requests left in the last hour | The daedalus count. A 429 from the provider sets it to 0. |

The header rows stay in memory only, so they are empty after a restart until the next answer. A client with its own provider key has its own rows. A bar like the weight bar shows the rest of each limit: header rows, OpenRouter credit and free requests, and Cloudflare neurons. The number and the bar show the same quantity.

The Limit column of a header row shows its short unit, such as `RPM` for requests per minute, and a hover spells it out. A count of 0 shows in red. From 1,000, a count shows floored to K, M or B, for example 998M of 1B. Hover a header row to see the exact number.

Each balance comes from the main key of the provider, not from `client_keys`.

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
