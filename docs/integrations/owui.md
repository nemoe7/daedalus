# Open WebUI integration

The `integrations/openwebui` folder holds 1 directory for each Open WebUI plugin type: `skills/`, `functions/` for the filters and pipes, and `tools/` for the Workspace Tools.

Each file is 1 plugin: paste its content in **Workspace → Tools** or **Workspace → Skills**, or import the file. The sections below name the install steps and the valves of each plugin.

## Deep research skill

`integrations/openwebui/skills/deep-research.md` is an Open WebUI skill: plain instructions, no code. The model plans, searches in 3 to 5 rounds with `search_web`, reads pages with `fetch_url`, and writes a report with numbered sources. Each step is a normal chat request, so daedalus failover and loop checks apply.

1. **Workspace → Skills**, the arrow next to **Create**, **Import JSON**. Select `deep-research.md`, then **Save**.
2. **Access** on the skill: make it public, or give read access to each user. A user without read access does not receive the skill.
3. **Workspace → Models**, **Create**: base model `daedalus/sophos`, name `Deep Research`. In **Skills**, select `deep-research`. **Save**.
4. In a chat with `Deep Research`, keep **Web Search** on. Use `$deep-research` in a chat with another model.

## GitHub tool

`integrations/openwebui/tools/github.py` is an Open WebUI Workspace Tool: one Python file, standard library only, empty `requirements`. It holds the GitHub surface of the reference connector. Reads run at once. Each write asks for confirmation in the chat.

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
2. **Valves**: set `github_token`, or set the `GITHUB_TOKEN` environment value. Keep `default_mode` `ask`.
3. **Access** on the tool: make it public, or give read access to each user. A user without read access does not see the tool.

| Valve | Default | Meaning |
| --- | --- | --- |
| `github_token` | empty | The token. `GITHUB_TOKEN` is the fallback. |
| `default_mode` | `ask` | `ask` gates each write, `allow` skips the dialog, `deny` refuses each write. |
| `timeout_seconds` | `60` | The wait for the confirmation. A closed tab, no dialog, or a late answer reads as no. |
| `http_timeout_seconds` | `30` | The GitHub HTTP timeout. |
| `api_version` | `2022-11-28` | The `X-GitHub-Api-Version` header. |
| `max_files_per_commit` | `30` | The file limit of `create_pr_with_files`. |
| `max_tree_entries` | `2000` | The entry limit of `list_tree`. |

`UserValves.mode` gives each user `ask`, `allow`, `deny`, or `default` for the valve. `UserValves.timeout_seconds` gives each user their own confirmation wait. A value of `0` keeps the valve.

The confirmation travels over the socket of the chat tab. `WEBSOCKET_EVENT_CALLER_TIMEOUT` is unset by default, so the Open WebUI server waits without a timeout when the browser does not answer. Only `timeout_seconds` ends that wait. A page refresh drops the dialog, the tool returns `denied: true`, and it sends nothing.

`tests/integrations/test_owui_github_tool.py` holds the tool surface, the gate and the error path.

## Google tool

`integrations/openwebui/tools/google.py` is a second Workspace Tool: 1 file, standard library only, empty `requirements`. It holds Gmail, Calendar, Drive and Docs. Reads run at once. Each write asks for confirmation in the chat.

1. In the Google Cloud console, make an OAuth client of the type Desktop app, and turn on the Gmail, Calendar, Drive and Docs APIs.
2. Do the consent 1 time, with the scopes `gmail.readonly`, `gmail.send`, `calendar.readonly`, `calendar.events`, `drive.readonly` and `documents`. The reply holds the refresh token.
3. **Valves**: set `google_client_id`, `google_client_secret` and `google_refresh_token`. Keep `default_mode` `ask`.

| Valve | Default | Meaning |
| --- | --- | --- |
| `google_client_id` | empty | The OAuth client id. |
| `google_client_secret` | empty | The OAuth client secret. |
| `google_refresh_token` | empty | The refresh token of the 1-time consent. The tool trades it for an access token and caches that for an hour. |
| `calendar_id` | `primary` | The calendar the agenda reads. |
| `max_results` | `10` | The default page of a list read. |
| `default_mode` | `ask` | `ask` gates each write, `allow` skips the dialog, `deny` refuses each write. |
| `timeout_seconds` | `60` | The wait for the confirmation. A closed tab, no dialog, or a late answer reads as no. |
| `http_timeout_seconds` | `30` | The Google HTTP timeout. |

The 5 reads are `search_mail`, `read_thread`, `agenda`, `search_files` and `read_document`. The 3 gated writes are `send_mail`, `create_event` and `append_to_document`. `UserValves.mode` and `UserValves.timeout_seconds` work as they do on the GitHub tool.

`tests/integrations/test_owui_google_tool.py` holds the surface, the gate, the token cache and the error path.

## Knowledge manager tool

`integrations/openwebui/tools/knowledge_manager.py` is a third Workspace Tool: 1 file, no valves, and the caller token comes from the chat request. It holds the 23 knowledge tools.

The knowledge tools list, create, read, update, move and delete knowledge bases, folders and files. They also search the files and read 1 by path. A file creation waits until Open WebUI finishes the indexing.

A new knowledge base attaches itself to the model presets in the same call. The tool reads each preset, merges `meta.knowledge` and posts the record back. It skips a preset without write access and names it. A knowledge item stays a reference object.

The preset helpers stay private, so Open WebUI builds no model tool spec for them. `/model/update` needs the owner, a write grant or an admin. The tool sends to `http://127.0.0.1:8080/api/v1`. Change `base_url` for another host.

1. **Workspace → Tools**, **Create**, paste the file, **Save**.
2. **Access** on the tool: make it public, or give read access to each user.

`tests/integrations/test_knowledge_manager_presets.py` holds the merge, the full-record write, the read-only skip, the all-presets loop and the attach on create.

## Skills manager tool

`integrations/openwebui/tools/skills_manager.py` is the Fu-Jie Skills Manager Tool 0.3.4: 1 file, and it manages the native Workspace Skills. The header keeps the author and the version. The local copy adds the preset attach.

The 6 skill tools are `list_skills`, `show_skill`, `install_skill`, `create_skill`, `update_skill` and `delete_skill`. An install fetches a skill from a trusted domain. A destructive action asks for confirmation.

A new skill attaches itself to the model presets in the same call. `create_skill` and the install path of `install_skill` read each preset, merge `meta.skillIds` and post the record back. The run skips a preset without write access and names it.

The preset helpers stay private, so Open WebUI builds no model tool spec for them. The calls use the OpenWebUI API with the caller token. The `OWUI_API_BASE` valve holds the base URL, by default `http://127.0.0.1:8080/api/v1`.

1. **Workspace → Tools**, **Create**, paste the file, **Save**.
2. **Access** on the tool: make it public, or give read access to each user.

| Valve | Default | Meaning |
| --- | --- | --- |
| `SHOW_STATUS` | `true` | Draw the status line of each operation. |
| `REQUIRE_CONFIRMATION` | `true` | Ask before an update, a delete or an overwrite. |
| `ALLOW_OVERWRITE_ON_CREATE` | `true` | Let a create or an install replace a skill of the same name. |
| `INSTALL_FETCH_TIMEOUT` | `12.0` | The URL fetch timeout of an install, in seconds. |
| `TRUSTED_DOMAINS` | `github.com,huggingface.co,githubusercontent.com` | The domains an install may fetch from. |
| `OWUI_API_BASE` | `http://127.0.0.1:8080/api/v1` | The OpenWebUI API base of the preset attach. |

`tests/integrations/test_skills_manager_presets.py` holds the merge, the full-record write, the read-only skip, the all-presets loop and the attach on create.
