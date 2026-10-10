# Dashboard

The dashboard is the browser view of a daedalus server: the overview, the requests, the models, the
notifications, the providers, the limits and the settings.

Open `http://HOST:3357/`.

| Login | Value |
| --- | --- |
| User | `DAEDALUS_USERNAME`, else `admin`. The form fills in `admin` only when `DAEDALUS_USERNAME` is not set. |
| Password | Reads `DAEDALUS_PASSWORD`, else `DAEDALUS_MASTER_KEY`. Without a password, the form shows a master key hint. |
| Remember me | On at the start: 30 days. Else the session ends after 12 h. |
| Change | A new master key, user or password stops all sessions. |
| Too many tries | A failed try waits 0.5 s. After 5 failures in a row, the client waits 60 s before another try. A good login sets the count back to 0. |

## Contents

- [Pages](#pages)
- [Demo on GitHub Pages](#demo-on-github-pages)
- [Requests](#requests)
- [Models](#models)
- [Notifications](#notifications)
- [Providers](#providers)
- [Limits](#limits)
- [A failed read](#a-failed-read)
- [API keys](#api-keys)

## Pages

| Page | Contents |
| --- | --- |
| Overview | 4 numbers on top: in flight, success rate, lowest limit left, models. Then the status, the last 5 requests, the limits and the pools in columns. Each pool row names its top model. The page shows state only: the Providers and Settings pages hold the configuration. |
| Requests | The log of the last model calls and the ones in flight. See [Requests](#requests). |
| Models | The catalog table and a card for each pool. See [Models](#models). |
| Notifications | The catalog rebuilds with their diffs, the update check, and the lane rows near their limit. |
| Providers | A tab per provider file. Form: a section per provider. YAML: the file text, as the last section. |
| Limits | The last rate-limit headers of each model, and each provider key balance in a card. |
| Settings | Form and YAML sections of [`config/daedalus.yml`](../config/daedalus.yml). A write checks the value, then reloads the settings. An API keys section makes and deletes keys. |

The logo shows in the header, on the login page and as the tab icon. The page has a web app manifest, so a browser can install the dashboard as an app. A browser installs it only over HTTPS or from localhost. The version shows under the name, also on the login page before a login: the image tag, for example v0.2.0, or dev-COMMIT after `install --dev`.

`daedalus --version` shows the same value. The header stays at the top of the window. Only the page below it scrolls. A fade at an edge of the tab bar shows the tabs that wait off screen.

A hidden browser tab sends no requests. It gets new data when it shows again.

The header shows the state of daedalus as text, in the style of the page switcher. A narrow
header drops the state, as a phone does.

The Overview page shows the state in its own card at every width. It holds the health line,
then a line for the sessions and a line for the catalog, each with its value at the right. The
catalog line gives the time of the last catalog rebuild. It also gives the next scheduled
rebuild.

The header keeps its Log out button on a phone. Log out opens a confirmation dialog. Click the catalog line to rebuild the catalog now, after a confirmation. The line shows "rebuilding" until the rebuild ends.

Only 1 rebuild runs at a time. A confirmation of a dangerous action uses a dialog of the dashboard, not a browser dialog.

## Demo on GitHub Pages

A static demo of this page runs at <https://nemoe7.github.io/daedalus/>. Set the Pages source of the repository to **GitHub Actions** 1 time, and the Pages demo workflow deploys each change to the page. No server runs there. [`scripts/pages_demo.py`](../scripts/pages_demo.py) copies the page and adds `demo.js`.

That script answers each `ui/api/` call from [`scripts/pages_fixtures.json`](../scripts/pages_fixtures.json). The Requests tab starts empty, and the script streams the captured requests in, then keeps the table rotating. A save stays in the page memory, and it goes through the checks of the server. Those checks cover the YAML of a file, the block of a provider name, and the rules of a key name.

One state holds the demo together. The provider files build the catalog and the pools. The requests move the weights, the cooldowns, the lane counters and the balance cards. A fake upstream answers each provider with its plan, so a save and a check now agree with the tables.

A reload brings the captured data back. The Catalog line of the status card starts a rebuild in the page, and it finishes after about 26 seconds. The header shows the version `demo`.

The Pages demo workflow builds the demo from `main` and deploys it after a change to the page, the script or the fixtures. After a change to an endpoint, run `python3 scripts/pages_demo.py --capture` and commit the new fixtures.

## Requests

The name of a model reads as its id does. It draws 3 marks: the provider, the developer when that
name differs, and the model part. A `/` stands between the parts, as in `provider/clef`. A name with a shipped
SVG file under [`daedalus/dashboard/ui/icons/`](../daedalus/dashboard/ui/icons) shows that mark, and a name with no file shows its own text.

The folder holds the marks of
`lobehub/lobe-icons` (MIT), and 1 new file with the name in the model id adds a mark. The hover
text and the cell title keep the full `provider/slug`.

The bar above the table narrows the view: a text match over the model, the served model, the client
app, the session and the status. A status filter (all, 2xx only, errors only) and a time range (all,
the last hour, the last 24 hours) narrow it too. The count at the right of the bar names the shown
rows: the view narrows in the page, so no new call goes out. The filters ride in the hash, such as
`#/requests?status=bad&hours=24&q=timeout`, so a shared link shows the same view.

A **Clear filters** button shows while a filter is live.

The page lists the model calls in flight at the top, then the last ones, 50 at a time and 500 at most.
The Live button pauses the in-flight rows and counts the requests that arrive while the pause holds.
A second click shows them and resumes. A click on a request opens its attempts, and a hover on a
count or name shows the exact value.

Each time reads as an age, such as `4 mins ago`, and its title holds the exact stamp. A request that
the client closed shows the code `499`. An exact string, such as a version, a session id or an API
key, reads in the mono font.

The pool of the auto model rides in the model name, such as `daedalus/auto/moros`. The routing codes beside a name, such as `lmt`, `frX` and `tlN`, are 3 characters. The Code legend card beside the table gives the meaning of each code. A hook file that defines `on_init` adds its rows below the base rows of that card.

A wide screen keeps the table in its own panel, so the head stays in view. The panel scrolls sideways, so the page never scrolls sideways. On a phone, the page scrolls and the rows become cards.

## Models

A card for each pool shows above the table, with its top model and its mean weight. A click on a card sets the filters of that pool.

A phone shows 1 card for each model: the name on its own line, then `Tier`, `Tools`, `Cooldown` and
`Weight` as 1 small row. The `Sort` control in the toolbar takes the place of the column heads.

| Control | Effect |
| --- | --- |
| Search | A text match over the provider and the slug |
| Type | 1 model type: chat, embedding, transcription, speech, image, video, decisions or rerank. The Media entries cover image input, PDF input, audio input and audio output. |
| Tier chips | `TIER-A` to `TIER-D` |
| Column head | A sort by that column |
| Model row | Opens the modal of the model: every fact of its row, the supported efforts included. |

| Column | Shows |
| --- | --- |
| Model | The provider mark, the developer mark and the model part, as on the Requests page |
| Type | The catalog mode and its flags |
| Tier | The tier letter, with the full `TIER-*` name on hover |
| Order | The order of the model inside its tier. Muted at 1. |
| Context | `max_input_tokens`, floored to K, M or B. The hover shows the exact number. |
| Tools | `supports_function_calling`, for a chat model |
| Reasoning | `supports_reasoning`, for a chat model |
| Cooldown | The time left of the cooldown |
| Weight | The weight bar and the value, 0.01 to 1 |

**Reset weights and cooldowns** sets each weight back to 1 and ends each cooldown. Session models stay.

The bar above the table narrows the rows: a text match, a type and a tier. A pool card of the
Overview page links to a set of those filters, such as `#/models?tier=C&mode=chat&sort=weight`.
The page drops the hash after it reads it.

## Notifications

| Item | Value |
| --- | --- |
| Rebuilds | 1 row per catalog rebuild: the time, the reason, the model count after the rebuild, and the diff. |
| Reason | `scheduled` is the 6-hour clock, `manual` the rebuild button, and a config save the save of a provider file. |
| Diff | `+N` rows added, `-N` rows deleted, `~N` rows whose stored columns moved. A click on a row lists the models, and a second click closes the list. |
| Failed | The providers whose model list failed in the rebuild. |
| Rebuild storage | The `catalog_rebuilds` table of `.daedalus-state/models.sqlite3`, the 200 newest events. |
| Update check | The version that goes after this build. A build from a `v` tag reads the latest release of `updates.repo`. A source build reads the head of `main` and compares its own commit. |
| Update cadence | At most 1 check per 6 h, on the tick of the catalog schedule. **Check now** runs 1 at once. The last check and its time stay in the `updates` table, so the next start shows them. |
| Limit warnings | The lane rows of the [Limits](#limits) page with a quarter or less of their limit left, the lowest share first. |

## Providers

The cards of the Form view stack in columns. The page carries no Save bar: a row writes itself. A value row
shows its value as text, and a click opens the small editor of that 1 value. The close of that dialog writes
it.

A switch and a pick list hold 1 valid value, so the change writes at once. The last write shows its
Undo beside the row that changed, and a failure shows its line under the file chips. The file row
holds 2 actions beside the file chips. They are **+ New provider**, and **Delete file** for a file
that is not the main one.

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
| Provider values | Catalog columns at the provider level, for example `reasoning_effort` or `rpm` | `key: value` chips. A model override wins. |

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
| Undo | A back arrow beside the row of the last write. It puts the file text of the moment before that write back. |
| **+ key** | A key list and a value input. Model overrides also take `pool` and `timeout`. |
| **+ Pattern** | Adds a pattern row with no overrides |
| Change of a pattern | Renames the pattern. The row keeps its position. |
| A value row | Opens the small editor. **Save** writes that 1 value, then reloads the configuration. |
| Ctrl+S | Writes the text of the YAML view, and does nothing in the Form view. |
| **New provider** | Asks for the provider name, then makes `config/providers/{name}.yml` with 1 card |
| **Delete file** | Deletes the `{provider}.yml` file after a confirmation. The main file stays. |

A value of `true` or `false` becomes a boolean. A value with digits only becomes a number. Other values stay text.

A write from the Form view keeps the comments, the key order, the quotes and the one-line maps of the file. It changes only the lines of the values that changed. An empty list or map leaves the file. An empty pattern row stays, because a pattern alone declares a model.

A change to the other view asks for a confirmation when the file holds unsaved changes. The other view shows the saved file.

The Settings rows write the same way. A switch and a pick list write on the change. A text or a number row
writes when its editor closes. A chip list writes on the add or the drop.

The Settings page keeps no bar: the rail starts the page, and only an error shows a line.

The YAML editor ends the section list of both pages. It is the fallback: a key or a block that the form cannot show still opens there. That card carries its own Save. Ctrl+S reaches it too.

A write of a file that is not valid YAML, or that has a key 2 times in 1 map, gets an error message. The file does not change. This rule applies to the Providers page and the Settings page. A refused value stays in its row, and the next write of that row takes it again.

A provider file on disk that is not valid YAML still gets its tab. It opens in the YAML view, with the error line next to the Save button. Fix the text there and save it.

The form holds the section list of the current file: the rail names every provider of the file, and the pane shows the picked card.
A narrow screen shows the list alone, and a pick opens 1 provider, with a button back to the list.
A field hint sits behind the info icon next to its label. The `Tiers`, `Model overrides` and
`Provider values` groups start closed, and their icon carries the group hint.

## Limits

daedalus reads the balances when it starts, then each hour. **Check now** reads them at once. The filter above
the table narrows it to 1 model, and it rides in the hash, such as `#/limits?q=llama`. A **Clear filters**
button shows while the filter is live.

A provider without a key or without data does not show.

| Provider | Values | Source |
| --- | --- | --- |
| OpenRouter | Credit left of the key limit, credit used today, free requests left today | `GET /api/v1/key` |
| Kilo | Account balance | `GET https://api.kilo.ai/api/profile/balance` |
| Pollinations | Pollen left. A key without a budget needs the `account:usage` scope. | `GET /account/balance` |
| Cloudflare | Neurons left since 00:00 UTC, of the free 10,000. | GraphQL `aiInferenceAdaptiveGroups` |
| Groq, Mistral | The limit, the count left and the reset time of each window, for each model | The `x-ratelimit-*` headers of the last answer |
| Kilo, and each provider with `hourly_requests` | In the card of the provider: the requests left in the last hour | The daedalus count. A 429 from the provider sets it to 0. |

The rows and the cards come back after a restart, from the state file. The `Seen` and `Checked` times mark
each reading. A client with its own provider key has its own rows. A bar like the weight bar shows the rest of
each limit: header rows, OpenRouter credit and free requests, and Cloudflare neurons.

The number and the bar show the same quantity.

The Limit column of a header row shows its short unit, such as `RPM` for requests per minute, and a hover spells it out. A count of 0 shows in red. From 1,000, a count shows floored to K, M or B, for example 998M of 1B. Hover a header row to see the exact number.

Each balance comes from the main key of the provider, not from `client_keys`.

To show the Cloudflare neurons, give the Cloudflare token the analytics permission:

1. In the Cloudflare dashboard, open **Manage account → Account API tokens**. For a user token, open **My Profile → API Tokens**.
2. On the token in `CLOUDFLARE_API_KEY`, select **Edit**.
3. Add a permission row: **Account**, **Account Analytics**, **Read**.
4. Select **Continue to summary**, then **Update token**. The token value does not change.

To test the token, send the query of [`daedalus/routing/limits.py`](../daedalus/routing/limits.py) with curl. Without the permission, the answer has an `errors` list.

## A phone

The Settings page and the provider form show their section list alone on a phone. Each row carries a chevron, and a pick opens 1 section as its own page. The section rides in the hash as `#/settings?section=timeouts`, so the back gesture of the phone returns to the list. The button above the card names that list, as `‹ Settings`, for a phone without the gesture.

A phone keeps the 4 numbers of the Overview above the status card, in 2 rows of 2. The Models table
and the API keys table become cards, as the Requests table does. A phone card carries its own border:
the wrapper behind the cards paints nothing. A chevron at an end of the tab bar scrolls the tabs that
wait off screen, and it goes at the end of the list.

## A failed read

A read that fails shows 1 line at the top of the page, with the message of the server and a **Try again**
button. A later read that lands hides the line. Each write note, such as the save of a provider file, carries
`role="status"`, so a screen reader reads it.

## API keys

| Rule | Value |
| --- | --- |
| Format | `sk-` and 43 characters |
| Name | 1 to 40 characters, unique |
| Storage | SHA-256 hash only. The dashboard shows the key 1 time. |
| Access | `/v1` only. Not the dashboard. |
| Log | `key=NAME` |

The form and the table ride in their own section of the Settings page.

Give each application its own key. Then you can delete 1 key after a leak, and the other applications continue to work.

Note: do not share daedalus with other people. The provider terms of service can forbid it. Use it for your own work only.
