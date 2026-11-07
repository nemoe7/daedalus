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

  function upload(id, automaticRetry = false) {
    if (inflight.has(id)) return inflight.get(id);
    const item = entries().find((entry) => entry.id === id);
    if (!item) return Promise.resolve();
    const request = (async () => {
      try {
        const attachment = await uploadFile(item.file, { automaticRetry });
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
      const added = Array.from(files || [], (file) => {
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
    async retry(id) {
      const item = entries().find((entry) => entry.id === id);
      if (!item || item.status !== "failed") return;
      update(entries().map((entry) =>
        entry.id === id ? { ...entry, status: "uploading" } : entry));
      await upload(id);
    },
    async readyForSend(ids) {
      await Promise.all(ids.map((id) => inflight.get(id)).filter(Boolean));
      const failed = ids.filter((id) => entries().find((entry) => entry.id === id)?.status === "failed");
      if (failed.length) {
        const failedSet = new Set(failed);
        update(entries().map((entry) =>
          failedSet.has(entry.id) ? { ...entry, status: "uploading" } : entry));
        await Promise.all(failed.map((id) => upload(id, true)));
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
