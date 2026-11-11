const MIME_EXT_MAP = {
  "image/png": ".png",
  "image/jpeg": ".jpg",
  "image/webp": ".webp",
  "image/gif": ".gif",
  "image/svg+xml": ".svg",
  "image/bmp": ".bmp",
  "image/avif": ".avif",
  "text/plain": ".txt",
  "text/markdown": ".md",
  "application/json": ".json",
  "application/pdf": ".pdf",
};

function inferUploadExtension(file) {
  const rawName = String(file?.name || "").trim();
  const dot = rawName.lastIndexOf(".");
  if (dot >= 0 && dot < rawName.length - 1) {
    const ext = rawName.slice(dot).replace(/[^A-Za-z0-9.]/g, "").slice(0, 20);
    if (ext.length > 1) return ext.toLowerCase();
  }
  const mime = String(file?.type || "").split(";", 1)[0].trim().toLowerCase();
  return MIME_EXT_MAP[mime] || ".bin";
}

function normalizeComposerFile(file, epochMs) {
  const rawName = String(file?.name || "").split(/[\\/]/).pop().trim();
  const dot = rawName.lastIndexOf(".");
  const stem = (dot > 0 ? rawName.slice(0, dot) : dot === 0 ? "" : rawName).replace(/^[.-]+|[.-]+$/g, "");
  if (stem) return file;
  const fallbackName = `${epochMs}${inferUploadExtension(file)}`;
  if (typeof File === "function" && file instanceof Blob) {
    return new File([file], fallbackName, { type: file.type || "" });
  }
  return { ...file, name: fallbackName };
}

// Track staged file uploads independently of the composer view.
export function createComposerUploads(store, uploadFile) {
  let nextId = 0;
  const sessionId = crypto.randomUUID();
  const inflight = new Map();
  const entries = () => store.getState().composerAttachments || [];
  const update = (items) => store.setState({ composerAttachments: items });
  const revokePreview = (item) => {
    if (item?.previewUrl && typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(item.previewUrl);
  };

  function upload(id) {
    if (inflight.has(id)) return inflight.get(id);
    const item = entries().find((entry) => entry.id === id);
    if (!item) return Promise.resolve();
    const request = (async () => {
      try {
        const attachment = await uploadFile(item.file);
        if (!attachment) throw new Error("Upload returned no attachment");
        update(entries().map((entry) =>
          entry.id === id ? { ...entry, status: "ready", attachment } : entry));
      } catch (error) {
        console.error(error);
        update(entries().map((entry) =>
          entry.id === id ? { ...entry, status: "failed" } : entry));
      }
    })();
    inflight.set(id, request);
    request.finally(() => {
      if (inflight.get(id) === request) inflight.delete(id);
    });
    return request;
  }

  return {
    async stage(files) {
      const baseMs = Date.now();
      const added = Array.from(files || [], (rawFile, idx) => {
        const file = normalizeComposerFile(rawFile, baseMs + idx);
        const image = /^image\//i.test(file.type || "") || /\.(?:png|jpe?g|gif|webp|svg|bmp|avif|ico)$/i.test(file.name || "");
        let previewUrl = "";
        if (image && typeof URL.createObjectURL === "function") {
          try { previewUrl = URL.createObjectURL(file); } catch { /* Keep a file icon. */ }
        }
        return { id: `${sessionId}:${++nextId}`, name: file.name, file, status: "uploading", previewUrl };
      });
      update([...entries(), ...added]);
      await Promise.all(added.map((entry) => upload(entry.id)));
    },
    async readyForSend(ids) {
      await Promise.all(ids.map((id) => inflight.get(id)).filter(Boolean));
      const failed = ids.filter((id) => entries().find((entry) => entry.id === id)?.status === "failed");
      if (failed.length) {
        const failedSet = new Set(failed);
        update(entries().map((entry) =>
          failedSet.has(entry.id) ? { ...entry, status: "uploading" } : entry));
        await Promise.all(failed.map((id) => upload(id)));
      }
      const selected = ids.map((id) => entries().find((entry) => entry.id === id));
      if (selected.some((entry) => !entry || entry.status !== "ready")) {
        throw new Error("A file did not upload. Check the connection and send again.");
      }
      return selected.map((entry) => entry.attachment);
    },
    remove(id) {
      const item = entries().find((entry) => entry.id === id);
      revokePreview(item);
      update(entries().filter((entry) => entry.id !== id));
    },
    clearSent(ids) {
      const sentIds = new Set(ids);
      for (const item of entries()) {
        if (sentIds.has(item.id)) revokePreview(item);
      }
      update(entries().filter((entry) => !sentIds.has(entry.id)));
    },
  };
}
