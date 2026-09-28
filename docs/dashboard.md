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
| Pools | The models of each pool, the media pools included, the highest weight first. "+N more" opens the Models page with the filters of the pool. |
| Requests | Each request: model, pool, the model that answered, time to first token, fallbacks and each attempt. A cooldown mark shows when an attempt started a cooldown. Errors have a copy button. |
| Models | All catalog models, with a type filter, a tier filter, column sort, a reasoning column, the time left of each cooldown and weight bars |
| API keys | Make and remove API keys |
| Providers | YAML editor for `config/providers/free.yml`. It keeps the comments. |
| Settings | Form for `config/daedalus.yml`. The mouse wheel changes the last decimal digit of a decimal field. |

The Requests page shows "koinos from moros" when a `daedalus/auto` request starts in moros and a koinos model answers. It shows "try again N" for a [try again](architecture.md#try-again) in Open WebUI.

## API keys

| Rule | Value |
| --- | --- |
| Format | `sk-` and 43 characters |
| Name | 1 to 40 characters, unique |
| Storage | SHA-256 hash only. The dashboard shows the key 1 time. |
| Access | `/v1` only. Not the dashboard. |
| Log | `key=NAME` |

Give each application its own key. Then you can remove 1 key after a leak, and the other applications continue to work.

Note: do not share Daedalus with other people. The provider terms of service can forbid it. Use it for your own work only.
