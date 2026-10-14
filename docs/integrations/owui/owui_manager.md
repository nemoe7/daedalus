# The manager tool

See [Open WebUI integration](../owui.md) for the other plugins.

[`integrations/openwebui/tools/owui_manager.py`](../../../integrations/openwebui/tools/owui_manager.py) is 1 Workspace Tool over the whole workspace: knowledge bases, skills, the file library, the Workspace Tools and the Functions. The caller token comes from the chat request, and the `OWUI_API_BASE` valve holds the base URL.

```mermaid
flowchart TD
  A[Chat] --> B[The model calls a tool]
  B --> C{Read, new item, or mutation?}
  C -- read --> D{permissions?}
  C -- new item --> D
  C -- mutation --> D
  D -- Always ask --> E[The confirmation dialog, with the item name]
  D -- Allow reads, read or new --> F[The Open WebUI API]
  D -- Allow reads, mutation --> E
  D -- Always allow --> F
  E -- click --> F
  F --> G[A new knowledge base, skill, tool or function joins the presets]
```

| Group | Tools |
| --- | --- |
| Knowledge | The 23 knowledge tools: bases, folders, files, search, tree. A file creation waits for the indexing |
| Files | `list_files`, `search_files`, `upload_file`, `rename_file`, `read_file_content`, `delete_file` |
| Skills | `list_skills`, `show_skill`, `list_skill_files`, `read_skill_file`, `install_skill`, `create_skill`, `update_skill`, `update_skill_files`, `toggle_skill`, `delete_skill` |
| Tools | `list_tools`, `show_tool`, `create_tool`, `update_tool`, `toggle_tool`, `delete_tool` |
| Functions | `list_functions`, `show_function`, `create_function`, `update_function`, `toggle_function`, `delete_function` |

The `permissions` valve holds 3 levels. `Always ask` gates every call, including reads. `Allow reads` frees the
reads and the new items, and holds the mutations and the toggles. `Always allow` holds nothing.

The question names the item.

A new knowledge base, skill, tool or function joins the model presets in the same call. The tool reads each preset, merges the matching `meta` list and posts the record back. It skips a preset without write access and names it. The `PRESET_MODELS` valve selects the presets by id or by name, and an empty list serves each preset.

## Open WebUI v0.12 skills

### Read files

`show_skill` includes the current version and complete file manifest. `list_skill_files` can inspect that version again. `read_skill_file` pages a UTF-8 file by character offset and reports binary-file metadata without decoding the bytes.

### Create and update files

`create_skill` accepts `SKILL.md` as `content` plus optional UTF-8 supporting `files`. The legacy name/content call remains valid. `update_skill` remains the simple instructions update. `update_skill_files` adds `put`, `move` and `delete` operations while preserving untouched files.

Pass the version from `show_skill` as `expected_version_id` for optimistic concurrency. If this value is empty, the manager reads the current version immediately before saving. Agent-authored file writes are text-only.

### Import from a URL

`install_skill` uses Open WebUI's native URL importer. It imports every package in a GitHub repository, folder or `SKILL.md` URL. It also imports packages in a raw Markdown, JSON or ZIP URL. Imported packages retain all text and base64-encoded binary files.

The URL must use HTTPS, contain no credentials and match `TRUSTED_DOMAINS`. Open WebUI then applies its redirect, SSRF, size and package validation. The native URL-loader endpoint requires an Open WebUI administrator. Successfully imported skills join the selected presets.

`timeout_seconds` sets the wait for the confirmation, and `UserValves.mode` plus `UserValves.timeout_seconds` give each user their own gate, where `0` keeps the tool valve. Open WebUI v0.12.0 has no per-tool toggle route, so `toggle_tool` toggles the tool id in the preset `toolIds` lists.

The preset helpers stay private, so Open WebUI builds no model tool spec for them. `/model/update` needs the owner, a write grant or an admin.

| Valve | Default | Meaning |
| --- | --- | --- |
| `OWUI_API_BASE` | `http://127.0.0.1:8080/api/v1` | The Open WebUI API base |
| `PRESET_MODELS` | empty | The ids or names of the presets that take an attach. Empty serves each preset |
| `permissions` | `Always ask` | `Always ask` gates every call, reads included. `Allow reads` frees the reads and the new items. `Always allow` holds nothing |
| `timeout_seconds` | `60` | The wait for a confirmation. `0` keeps the valve |
| `INSTALL_FETCH_TIMEOUT` | `60.0` | The total wait for Open WebUI's native skill URL loader |
| `TRUSTED_DOMAINS` | `github.com,huggingface.co,githubusercontent.com` | The domains a skill install may fetch from |
| `SHOW_STATUS` | `true` | Draw the status line of each operation |

1. **Workspace → Tools**, **Create**, paste the file, **Save**.
2. **Access** on the tool: make it public, or give read access to each user.

[`tests/integrations/test_owui_manager.py`](../../../tests/integrations/test_owui_manager.py) holds the surface, gate, free reads and creates, preset filtering and attachment, plus the v0.12 URL-import and multi-file request shapes.
