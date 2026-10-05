# Open WebUI integration

The [`integrations/openwebui`](../../integrations/openwebui) folder holds 1 directory for each Open WebUI plugin type: [`skills/`](../../integrations/openwebui/skills), [`functions/`](../../integrations/openwebui/functions) for the filters and pipes, and [`tools/`](../../integrations/openwebui/tools) for the Workspace Tools. The message-toolbar actions sit in [`actions/`](../../integrations/openwebui/actions).

Each file is 1 plugin: paste its content in **Workspace → Tools** or **Workspace → Skills**, or import the file. The sections below name the install steps and the valves of each plugin.

## Deep research skill

[`integrations/openwebui/skills/deep-research.md`](../../integrations/openwebui/skills/deep-research.md) is an Open WebUI skill: plain instructions, no code. The model plans, searches in 3 to 5 rounds with `search_web`, reads pages with `fetch_url`, and writes a report with numbered sources. Each step is a normal chat request, so daedalus failover and loop checks apply.

1. **Workspace → Skills**, the arrow next to **Create**, **Import JSON**. Select `deep-research.md`, then **Save**.
2. **Access** on the skill: make it public, or give read access to each user. A user without read access does not receive the skill.
3. **Workspace → Models**, **Create**: base model `daedalus/sophos`, name `Deep Research`. In **Skills**, select `deep-research`. **Save**.
4. In a chat with `Deep Research`, keep **Web Search** on. Use `$deep-research` in a chat with another model.

## GitHub tool

[`integrations/openwebui/tools/github.py`](../../integrations/openwebui/tools/github.py) is an Open WebUI Workspace Tool: one Python file, standard library only, empty `requirements`. It holds the GitHub surface of the reference connector. The `permissions` valve sets the gate. `Always ask`, the shipped default, holds every call, reads included. `Allow reads` frees the reads and holds the writes.

3 tools sit outside the connector list. `create_pr_with_files` writes a file list as 1 commit and opens or updates the pull request. `list_commits` reads the history of a path or a branch. `list_tree` reads the whole tree in 1 call.

3 security reads cover the alert lists: `code_scanning_alerts`, `secret_scanning_alerts` and `dependabot_alerts`. Each lists with filters, or reads 1 alert with its instances or locations. They need a token with the `security_events` scope, or the matching fine-grained read. Without it GitHub answers 403.

2 CI reads cover the checks. `check_runs` lists the check runs of a ref with the name, the status and the filter, or reads 1 run with its annotations. `list_workflows` lists the Actions workflows.

4 more reads ship:

| Read | Use |
| --- | --- |
| `actions_minutes` | The Actions minutes of an org or a user |
| `releases` | The releases, or 1 by id or tag |
| `tags` | The tags |
| `packages` | The packages of the signed-in user, a user or an org |

Gist management ships as 5 tools: `gists`, `fetch_gist`, and the gated writes `create_gist`, `update_gist` and `delete_gist`.

The 3 alert reads take gated writes: `update_code_scanning_alert`, `update_secret_scanning_alert` and `update_dependabot_alert` dismiss, resolve or reopen an alert.

1. **Workspace → Tools**, **Create**, paste the file, **Save**.
2. **Valves**: set `github_token`, or set the `GITHUB_TOKEN` environment value. Set `permissions`. `Always ask` is the shipped default.
3. **Access** on the tool: make it public, or give read access to each user. A user without read access does not see the tool.

| Valve | Default | Meaning |
| --- | --- | --- |
| `github_token` | empty | The token. `GITHUB_TOKEN` is the fallback. |
| `permissions` | `Always ask` | `Always ask` gates every call, reads included. `Allow reads` frees the reads and holds the gated calls. `Always allow` holds nothing. |
| `timeout_seconds` | `60` | The wait for the confirmation. A closed tab, no dialog, or a late answer reads as no. |
| `http_timeout_seconds` | `30` | The GitHub HTTP timeout. |
| `api_version` | `2022-11-28` | The `X-GitHub-Api-Version` header. |
| `max_files_per_commit` | `30` | The file limit of `create_pr_with_files`. |
| `max_tree_entries` | `2000` | The entry limit of `list_tree`. |

The 22 reads are the issue, pull request, commit, workflow and profile reads. Under `Always ask` each one waits for the click, so no repository line reaches the chat. `Allow reads` frees them.

`UserValves.mode` gives each user the same 3 levels, and `default` follows the tool valve. `UserValves.timeout_seconds` gives each user their own confirmation wait. A value of `0` keeps the valve.

The confirmation travels over the socket of the chat tab. `WEBSOCKET_EVENT_CALLER_TIMEOUT` is unset by default, so the Open WebUI server waits without a timeout when the browser does not answer. Only `timeout_seconds` ends that wait. A page refresh drops the dialog, the tool returns `denied: true`, and it sends nothing.

[`tests/integrations/test_owui_github_tool.py`](../../tests/integrations/test_owui_github_tool.py) holds the tool surface, the gate and the error path.

## Google tool

[`integrations/openwebui/tools/google.py`](../../integrations/openwebui/tools/google.py) is a second Workspace Tool: 1 file, standard library only, empty `requirements`. It holds Gmail, Calendar, Drive and Docs. The `permissions` valve sets the gate. `Always ask`, the shipped default, holds every call, reads included. `Allow reads` frees the reads and holds the writes.

1. In the Google Cloud console, make an OAuth client of the type Desktop app, and turn on the Gmail, Calendar, Drive and Docs APIs.
2. Do the consent 1 time, with the scopes `gmail.readonly`, `gmail.send`, `calendar.readonly`, `calendar.events`, `drive.readonly` and `documents`. The reply holds the refresh token.
3. **Valves**: set `google_client_id`, `google_client_secret` and `google_refresh_token`. Set `permissions`. `Always ask` is the shipped default.

| Valve | Default | Meaning |
| --- | --- | --- |
| `google_client_id` | empty | The OAuth client id. |
| `google_client_secret` | empty | The OAuth client secret. |
| `google_refresh_token` | empty | The refresh token of the 1-time consent. The tool trades it for an access token and caches that for an hour. |
| `calendar_id` | `primary` | The calendar the agenda reads. |
| `max_results` | `10` | The default page of a list read. |
| `permissions` | `Always ask` | `Always ask` gates every call, reads included. `Allow reads` frees the reads and holds the gated calls. `Always allow` holds nothing. |
| `timeout_seconds` | `60` | The wait for the confirmation. A closed tab, no dialog, or a late answer reads as no. |
| `http_timeout_seconds` | `30` | The Google HTTP timeout. |

The 5 reads are `search_mail`, `read_thread`, `agenda`, `search_files` and `read_document`. The 3 gated writes are `send_mail`, `create_event` and `append_to_document`.

The shipped `Always ask` holds every call, reads included, so no mail line and no one time code reaches the chat without a click. `Allow reads` frees the reads and keeps the gate on the 3 writes. `Always allow` runs everything. `UserValves.mode` carries the same 3 values for one user, and `default` follows the tool valve.

[`tests/integrations/test_owui_google_tool.py`](../../tests/integrations/test_owui_google_tool.py) holds the surface, the gate, the token cache and the error path.

## Reasoning bump action

[`integrations/openwebui/actions/effort_bump.py`](../../integrations/openwebui/actions/effort_bump.py) is an Open WebUI Action: 1 file, the light bulb of the message toolbar. 1 press moves the chat 1 rung up the reasoning ladder of daedalus, and answers the pressed turn again on that rung.

1. Put [`config/hooks/auto_reasoning.py`](../../config/hooks/auto_reasoning.py) in the `config/hooks` folder of daedalus. The action calls the file by name, so a press without it answers 404.
2. **Admin Panel → Functions**, the **+** menu, **New Function**, type **Action**, paste the file, **Save**.
3. **Workspace → Models**, the model, the **Actions** selector: turn the action on. The id rides in the `meta.actionIds` list of the model, so only those models draw the button.
4. **Valves**: set `DAEDALUS_API_KEY`. Set `DAEDALUS_API_BASE` when daedalus does not sit on `http://127.0.0.1:3357`.

| Valve | Default | Meaning |
| --- | --- | --- |
| `DAEDALUS_API_BASE` | `http://127.0.0.1:3357` | The daedalus base URL |
| `DAEDALUS_API_KEY` | empty | The key of the daedalus chat, as a password field |
| `timeout_seconds` | `300` | The wait for 1 press, the new answer included |
| `SHOW_STATUS` | `true` | Draw the status line of the press |

1 press runs 2 steps. The action posts the chat to `/v1/hook/auto_reasoning`, which names the pool, the tier and the effort of the next rung. It then posts the turn to `/v1/chat/completions` with that pool and that effort, and returns the new text. Open WebUI keeps the old text as `originalContent` of the message, so the reader steps back with the arrows of the chat.

The same file holds the `on-prompt` point, which sets the reasoning level of each request from the heuristics v2 read. That point serves the `CLIENT` constant of the file, `OWUI` by default, so Kilo and a generic client keep their own effort.

[`tests/integrations/test_owui_effort_bump.py`](../../tests/integrations/test_owui_effort_bump.py) holds the 2 steps, the error paths and the returned message.

## The manager tool

[`integrations/openwebui/tools/owui_manager.py`](../../integrations/openwebui/tools/owui_manager.py) is 1 Workspace Tool over the whole workspace: knowledge bases, skills, the file library, the Workspace Tools and the Functions. The caller token comes from the chat request, and the `OWUI_API_BASE` valve holds the base URL.

| Group | Tools |
| --- | --- |
| Knowledge | The 23 knowledge tools: bases, folders, files, search, tree. A file creation waits for the indexing |
| Files | `list_files`, `search_files`, `upload_file`, `rename_file`, `read_file_content`, `delete_file` |
| Skills | `list_skills`, `show_skill`, `install_skill`, `create_skill`, `update_skill`, `delete_skill` |
| Tools | `list_tools`, `show_tool`, `create_tool`, `update_tool`, `toggle_tool`, `delete_tool` |
| Functions | `list_functions`, `show_function`, `create_function`, `update_function`, `toggle_function`, `delete_function` |

The `permissions` valve holds 3 levels. `Always ask` gates every call, reads included. `Allow reads` frees the reads and the new items, and holds the mutations and the toggles. `Always allow` holds nothing. The item name rides in the question.

A new knowledge base, skill, tool or function joins the model presets in the same call. The tool reads each preset, merges the matching `meta` list and posts the record back. It skips a preset without write access and names it. The `PRESET_MODELS` valve picks the presets by id or by name, and an empty list serves each preset.

`timeout_seconds` sets the wait for the confirmation, and `UserValves.mode` plus `UserValves.timeout_seconds` give each user their own gate, where `0` keeps the valve. Open WebUI v0.11.4 has no per-tool toggle route, so `toggle_tool` toggles the tool id in the preset `toolIds` lists.

The preset helpers stay private, so Open WebUI builds no model tool spec for them. `/model/update` needs the owner, a write grant or an admin.

| Valve | Default | Meaning |
| --- | --- | --- |
| `OWUI_API_BASE` | `http://127.0.0.1:8080/api/v1` | The Open WebUI API base |
| `PRESET_MODELS` | empty | The ids or names of the presets that take an attach. Empty serves each preset |
| `permissions` | `Always ask` | `Always ask` gates every call, reads included. `Allow reads` frees the reads and the new items. `Always allow` holds nothing |
| `timeout_seconds` | `60` | The wait for a confirmation. `0` keeps the valve |
| `INSTALL_FETCH_TIMEOUT` | `12.0` | The URL fetch timeout of a skill install |
| `TRUSTED_DOMAINS` | `github.com,huggingface.co,githubusercontent.com` | The domains a skill install may fetch from |
| `SHOW_STATUS` | `true` | Draw the status line of each operation |

1. **Workspace → Tools**, **Create**, paste the file, **Save**.
2. **Access** on the tool: make it public, or give read access to each user.

[`tests/integrations/test_owui_manager.py`](../../tests/integrations/test_owui_manager.py) holds the surface, the gate, the free reads and creates, the preset filter, the attach and the merge.
