// Kilo Code plugin: copies the Daedalus token limits and feature flags into the Kilo config.
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

const TAG = "[daedalus]";
const TIMEOUT_MS = 3000;

function authFile() {
  const data = process.env.XDG_DATA_HOME || join(homedir(), ".local", "share");
  return join(data, "kilo", "auth.json");
}

async function storedKey(providerID) {
  try {
    const entry = JSON.parse(await readFile(authFile(), "utf8"))[providerID];
    return entry && entry.type === "api" && typeof entry.key === "string" ? entry.key : "";
  } catch {
    return "";
  }
}

async function apiKey(providerID, options) {
  if (typeof options.apiKey === "string" && options.apiKey) return options.apiKey;
  return (await storedKey(providerID)) || process.env.DAEDALUS_API_KEY || "";
}

function isDaedalus(providerID, block) {
  if (!block || typeof block !== "object" || !block.models) return false;
  if (!block.options || typeof block.options.baseURL !== "string") return false;
  return providerID === "daedalus" || Object.keys(block.models).some((key) => key.startsWith("daedalus/"));
}

async function readModels(baseURL, key) {
  const headers = key ? { Authorization: `Bearer ${key}` } : {};
  const url = `${baseURL.replace(/\/+$/, "")}/models`;
  const response = await fetch(url, { headers, signal: AbortSignal.timeout(TIMEOUT_MS) });
  if (!response.ok) throw new Error(`GET ${url} -> HTTP ${response.status}`);
  const body = await response.json();
  if (!body || !Array.isArray(body.data)) throw new Error(`GET ${url} -> no data list`);
  return new Map(body.data.filter((row) => row && row.owned_by === "daedalus").map((row) => [row.id, row]));
}

// Output 0 lets Kilo use its own output default. A stale `limit.input` goes, so `context` is the only input ceiling.
function patch(model, row) {
  const limit = { ...(model.limit || {}), output: 0 };
  delete limit.input;
  if (Number.isFinite(row.max_input_tokens)) limit.context = row.max_input_tokens;
  const next = { ...model, limit };
  if (typeof row.supports_reasoning === "boolean") next.reasoning = row.supports_reasoning;
  if (typeof row.supports_function_calling === "boolean") next.tool_call = row.supports_function_calling;
  if (row.supports_vision === true) {
    next.modalities = { ...(model.modalities || {}), input: ["text", "image"] };
    next.attachment = true;
  }
  return next;
}

async function apply(config) {
  for (const [providerID, block] of Object.entries(config.provider || {})) {
    if (!isDaedalus(providerID, block)) continue;
    try {
      const rows = await readModels(block.options.baseURL, await apiKey(providerID, block.options));
      const patched = [];
      for (const [key, model] of Object.entries(block.models)) {
        const row = rows.get(key);
        if (!row || !model || typeof model !== "object") continue;
        block.models[key] = patch(model, row);
        patched.push(key);
      }
      console.error(TAG, providerID, "patched", patched.length, "models");
    } catch (error) {
      console.error(TAG, providerID, "fail open", error instanceof Error ? error.message : String(error));
    }
  }
}

export default {
  id: "daedalus",
  server: async () => ({ config: apply }),
};
