// extension/background.js を、偽の messenger に対して動かし、J-1〜J-7 を確かめる。
// 入力（標準入力の JSON）は Python 側が作る。禁止語をここに書かないため、名前は入力から受け取る。
// 出力（標準出力の JSON）: { scenarios: [{ name, ok, detail }], extra: {...} }
import fs from "node:fs";
import vm from "node:vm";

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const code = fs.readFileSync(input.backgroundPath, "utf8");
const scenarios = [];
const extra = {};

function record(name, ok, detail = "") {
  scenarios.push({ name, ok: Boolean(ok), detail: String(detail) });
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const WRITE_APIS = ["messages.update", "messages.move", "compose.beginNew", "compose.saveMessage", "tabs.remove", "windows.remove"];

function makeEnv(opts = {}) {
  const calls = [];
  const log = (name, ...args) => calls.push([name, ...args]);
  const folders = [
    { id: "f-inbox", accountId: "a1", name: "Inbox", path: "/Inbox", specialUse: ["inbox"] },
    { id: "f-arch", accountId: "a1", name: "Archives", path: "/Archives", specialUse: ["archives"] },
    { id: "f-proj", accountId: "a1", name: "Projects", path: "/Projects", specialUse: [] },
    { id: "f-custom", accountId: "a1", name: "Custom", path: "/Custom", specialUse: ["x-custom"] },
  ];
  const tags = [
    { key: "$label1", tag: "Important", color: "#f00" },
    { key: "work", tag: "Work", color: "" },
    ...input.dollarKeys.map((key) => ({ key, tag: key, color: "" })),
    { key: "c1" + String.fromCharCode(0x85) + "ctl", tag: "ctl", color: "" },
  ];
  const headers = new Map();
  for (let id = 1; id <= 60; id++) {
    headers.set(id, {
      id, subject: `Subject ${id}`, author: "a@example.com", date: (opts.dates && opts.dates[id]) || "2026-11-20T09:00:00Z",
      read: false, flagged: false, tags: id === 2 ? ["work"] : [], folder: { id: "f-inbox" },
    });
  }
  const posted = [];
  let listener = null;
  const port = {
    onMessage: { addListener: (fn) => { listener = fn; } },
    onDisconnect: { addListener: () => {} },
    postMessage: (message) => { posted.push(message); },
  };
  const file = opts.attachmentFile || { size: 3, arrayBuffer: async () => new Uint8Array([97, 98, 99]).buffer };
  const messenger = {
    runtime: {
      connectNative: (name) => { log("runtime.connectNative", name); return port; },
      getBrowserInfo: async () => ({ version: "140.0" }),
    },
    accounts: { list: async () => [{ id: "a1", name: "Acct", type: "imap" }] },
    folders: {
      get: async (id) => {
        const folder = folders.find((f) => f.id === id);
        if (!folder) throw new Error("no such folder");
        return folder;
      },
      query: async () => folders,
    },
    messages: {
      get: async (id) => {
        if (!headers.has(id)) throw new Error("no such message");
        return headers.get(id);
      },
      getFull: async () => ({ contentType: "multipart/mixed", parts: [{ contentType: "text/plain", body: "Hello body" }] }),
      listAttachments: async () => [{ partName: "1.2", name: "a.txt", contentType: "text/plain", size: 3 }],
      getAttachmentFile: async (id, part) => { log("messages.getAttachmentFile", id, part); return file; },
      query: async (q) => { log("messages.query", q); return { id: null, messages: [...headers.values()] }; },
      list: async (folderId) => { log("messages.list", folderId); return { id: null, messages: [...headers.values()] }; },
      update: async (id, props) => { log("messages.update", id, props); },
      move: async (ids, folderId) => { log("messages.move", ids, folderId); },
      tags: { list: async () => tags },
    },
    compose: {
      beginNew: async (details) => { log("compose.beginNew", details); return { id: 900, windowId: 901 }; },
      saveMessage: async (tabId, options) => {
        log("compose.saveMessage", tabId, options);
        if (opts.saveFails) throw new Error("save failed");
        return { messages: [{ id: 777, folder: { id: "f-drafts" } }], mode: "draft" };
      },
    },
    tabs: {
      remove: async (id) => { log("tabs.remove", id); if (opts.tabRemoveFails) throw new Error("cannot close"); },
    },
    windows: {
      remove: async (id) => { log("windows.remove", id); if (opts.windowRemoveFails) throw new Error("cannot close"); },
    },
  };
  const sandbox = {
    messenger, crypto: globalThis.crypto, TextEncoder, TextDecoder, btoa, atob, setTimeout,
    console: { error() {}, log() {} },
  };
  vm.runInNewContext(code, sandbox, { filename: "background.js" });

  async function request(op, args, id = 1) {
    const before = posted.length;
    listener({ id, op, args });
    for (let i = 0; i < 400; i++) {
      const found = posted.slice(before).find((m) => m && m.id === id && !("chunk" in m));
      if (found) return found;
      await sleep(5);
    }
    throw new Error(`no response for ${String(op)}`);
  }
  const writes = () => calls.filter((c) => WRITE_APIS.includes(c[0]));
  return { calls, posted, request, writes, feed: (message) => listener(message), port };
}

async function scenario(name, fn) {
  try {
    const outcome = await fn();
    record(name, outcome === undefined ? true : outcome, "");
  } catch (error) {
    record(name, false, error && error.stack ? error.stack : error);
  }
}

const refused = (response) => response.ok === false && response.code === "refused";
const ids = (n) => Array.from({ length: n }, (_, i) => String(i + 1));

// --- J-1: fixed op table -------------------------------------------------------------------
await scenario("J-1 connects to the native host by name", async () => {
  const env = makeEnv();
  return env.calls.some((c) => c[0] === "runtime.connectNative" && c[1] === "thunderbird_mcp_host");
});

for (const op of [...input.forbiddenOps, "messages.move", "", "health ", "constructor", "__proto__", "toString"]) {
  await scenario(`J-1 refuses op ${JSON.stringify(op)}`, async () => {
    const env = makeEnv();
    const response = await env.request(op, {});
    return refused(response) && env.calls.every((c) => c[0] === "runtime.connectNative");
  });
}
for (const op of [null, 5, {}, ["health"]]) {
  await scenario(`J-1 refuses non-string op ${JSON.stringify(op)}`, async () => {
    const env = makeEnv();
    return refused(await env.request(op, {}));
  });
}
await scenario("J-1 refuses arguments that are not an object", async () => {
  const env = makeEnv();
  return refused(await env.request("health", "x")) && refused(await env.request("health", null, 2));
});

// --- J-2: messages.update keys --------------------------------------------------------------
await scenario("J-2 update keys are only read, flagged or tags (one each)", async () => {
  const env = makeEnv();
  await env.request("messages.mark", { message_ids: ["1", "2"], read: true });
  await env.request("messages.flag", { message_ids: ["1"], flagged: false }, 2);
  await env.request("tags.update", { message_ids: ["1"], add: ["$label1"], remove: [] }, 3);
  const updates = env.calls.filter((c) => c[0] === "messages.update");
  const keySets = updates.map((c) => Object.keys(c[2]).join(","));
  return updates.length === 4 && keySets.join("|") === "read|read|flagged|tags";
});
await scenario("J-2 non-boolean flags are refused", async () => {
  const env = makeEnv();
  const a = await env.request("messages.mark", { message_ids: ["1"], read: "yes" });
  const b = await env.request("messages.flag", { message_ids: ["1"], flagged: 1 }, 2);
  return refused(a) && refused(b) && env.writes().length === 0;
});

// --- J-4: tag keys ----------------------------------------------------------------------------
const NEL_KEY = "c1" + String.fromCharCode(0x85) + "ctl";
const badTagKeys = ["has space", "tab\tkey", "paren(", 'quote"', "brace{", "percent%", "star*", "", "x".repeat(65), "\\Seen", "a]b", "$other", NEL_KEY];
for (const key of [...badTagKeys, ...input.dollarKeys, "unknown-key"]) {
  await scenario(`J-4 refuses tag key ${JSON.stringify(key)}`, async () => {
    const env = makeEnv();
    const response = await env.request("tags.update", { message_ids: ["1"], add: [key], remove: [] });
    return refused(response) && env.writes().length === 0;
  });
}
await scenario("J-4 a bad key refuses the whole request", async () => {
  const env = makeEnv();
  const response = await env.request("tags.update", { message_ids: ["1"], add: ["$label1", "unknown-key"], remove: [] });
  return refused(response) && env.writes().length === 0;
});
await scenario("J-4 removal keys are checked too", async () => {
  const env = makeEnv();
  const response = await env.request("tags.update", { message_ids: ["1"], add: [], remove: ["unknown-key"] });
  return refused(response) && env.writes().length === 0;
});
await scenario("J-4 known keys are applied (add one, remove another)", async () => {
  const env = makeEnv();
  const response = await env.request("tags.update", { message_ids: ["2"], add: ["$label1"], remove: ["work"] });
  const update = env.calls.find((c) => c[0] === "messages.update");
  return response.ok === true && JSON.stringify(update[2]) === JSON.stringify({ tags: ["$label1"] });
});

// --- J-5: at most 50 messages per write -----------------------------------------------------------
const writeOps = [
  ["messages.mark", (list) => ({ message_ids: list, read: true })],
  ["messages.flag", (list) => ({ message_ids: list, flagged: true })],
  ["tags.update", (list) => ({ message_ids: list, add: ["$label1"], remove: [] })],
];
for (const [op, build] of writeOps) {
  await scenario(`J-5 ${op}: 51 ids are refused`, async () => {
    const env = makeEnv();
    return refused(await env.request(op, build(ids(51)))) && env.writes().length === 0;
  });
  await scenario(`J-5 ${op}: no ids are refused`, async () => {
    const env = makeEnv();
    return refused(await env.request(op, build([]))) && env.writes().length === 0;
  });
  await scenario(`J-5 ${op}: 50 ids are accepted`, async () => {
    const env = makeEnv();
    return (await env.request(op, build(ids(50)))).ok === true;
  });
  await scenario(`J-5 ${op}: non-numeric ids are refused`, async () => {
    const env = makeEnv();
    return refused(await env.request(op, build(["1", "../2"]))) && env.writes().length === 0;
  });
}

// --- J-6: drafts ------------------------------------------------------------------------------------
const draft = { to: ["alice@example.com"], cc: [], bcc: [], subject: "Hello", body: "line1\nline2", identity_id: null };
await scenario("J-6 saves with mode draft only and closes the compose window", async () => {
  const env = makeEnv();
  const response = await env.request("drafts.create", draft);
  const names = env.calls.filter((c) => c[0].startsWith("compose.")).map((c) => c[0]);
  const save = env.calls.find((c) => c[0] === "compose.saveMessage");
  const close = env.calls.find((c) => c[0] === "tabs.remove");
  return (
    response.ok === true && response.result.message_id === "777" && response.result.folder_id === "f-drafts" &&
    names.join(",") === "compose.beginNew,compose.saveMessage" &&
    JSON.stringify(save[2]) === JSON.stringify({ mode: "draft" }) && close && close[1] === 900
  );
});
await scenario("J-6 falls back to closing the window", async () => {
  const env = makeEnv({ tabRemoveFails: true });
  const response = await env.request("drafts.create", draft);
  return response.ok === true && env.calls.some((c) => c[0] === "windows.remove" && c[1] === 901);
});
await scenario("J-6 reports an error when the window cannot be closed", async () => {
  const env = makeEnv({ tabRemoveFails: true, windowRemoveFails: true });
  const response = await env.request("drafts.create", draft);
  return response.ok === false && response.code !== "refused";
});
await scenario("J-6 a failed save is an error and the window is closed", async () => {
  const env = makeEnv({ saveFails: true });
  const response = await env.request("drafts.create", draft);
  return response.ok === false && env.calls.some((c) => c[0] === "tabs.remove");
});
const badDrafts = {
  "subject with CR LF": { ...draft, subject: "Hi\r\nBcc: evil@example.com" },
  "subject with bell": { ...draft, subject: "bell\u0007" },
  "address with CR LF": { ...draft, to: ["evil@example.com\r\nBcc: x@example.com"] },
  "display name address": { ...draft, to: ["Alice <alice@example.com>"] },
  "comma separated": { ...draft, to: ["a@example.com, b@example.com"] },
  "no dot in domain": { ...draft, to: ["a@b"] },
  "trailing newline": { ...draft, to: ["a@example.com\n"] },
  "no recipient": { ...draft, to: [] },
  "bad cc": { ...draft, cc: ["nope"] },
  "bad bcc": { ...draft, bcc: ["bad\r\n@example.com"] },
  "21 recipients": { ...draft, to: Array.from({ length: 21 }, (_, i) => `u${i}@example.com`) },
  "non-string body": { ...draft, body: 5 },
  "empty identity id": { ...draft, identity_id: "" },
};
for (const [name, args] of Object.entries(badDrafts)) {
  await scenario(`J-6 refuses draft: ${name}`, async () => {
    const env = makeEnv();
    return refused(await env.request("drafts.create", args)) && env.writes().length === 0;
  });
}
await scenario("J-6 draft arguments take no extra switch", async () => {
  const env = makeEnv();
  await env.request("drafts.create", { ...draft, immediate: true, mode: "now" });
  const begin = env.calls.find((c) => c[0] === "compose.beginNew");
  const allowed = new Set(["to", "cc", "bcc", "subject", "plainTextBody", "isPlainText", "identityId"]);
  return Object.keys(begin[1]).every((key) => allowed.has(key));
});

// --- J-7: health ----------------------------------------------------------------------------------------
await scenario("J-7 health reports the version and the draft mode", async () => {
  const env = makeEnv();
  const response = await env.request("health", {});
  return response.ok === true && response.result.thunderbird_version === "140.0" &&
    ["background", "compose_window"].includes(response.result.draft_mode);
});

// --- Reads and chunking ----------------------------------------------------------------------------------
await scenario("reads return mapped data and never write", async () => {
  const env = makeEnv();
  const got = await env.request("messages.get", { message_id: "1" });
  const query = await env.request("messages.query", { text: "Subject", limit: 5 }, 2);
  const list = await env.request("messages.list", { folder_id: "f-inbox", limit: 3, offset: 2 }, 3);
  const attachments = await env.request("attachments.list", { message_id: "1" }, 4);
  const read = await env.request("attachments.read", { message_id: "1", part_name: "1.2" }, 5);
  return (
    got.ok && got.result.body_text === "Hello body" && got.result.summary.has_attachments === true &&
    query.ok && query.result.length === 5 && list.ok && list.result.length === 3 && list.result[0].id === "3" &&
    attachments.ok && attachments.result[0].part_name === "1.2" &&
    read.ok && read.result.data_b64 === "YWJj" && env.writes().length === 0
  );
});
// SPEC §6: messages.list returns newest first, and offset/limit apply after sorting.
// The stub returns ids in ascending order, so id 60 is the newest only if the op sorts by date.
await scenario("messages.list sorts newest first before offset and limit", async () => {
  const dates = {};
  for (let id = 1; id <= 60; id++) dates[id] = new Date(Date.UTC(2026, 10, 1) + id * 3600 * 1000).toISOString();
  const env = makeEnv({ dates });
  const top = await env.request("messages.list", { folder_id: "f-inbox", limit: 3, offset: 0 }, 6);
  const paged = await env.request("messages.list", { folder_id: "f-inbox", limit: 2, offset: 1 }, 7);
  return (
    top.ok && top.result.map((m) => m.id).join(",") === "60,59,58" &&
    paged.ok && paged.result.map((m) => m.id).join(",") === "59,58"
  );
});
await scenario("messages.list keeps the original order for equal or missing dates", async () => {
  const env = makeEnv({ dates: { 1: "2026-11-25T00:00:00Z" } });
  const list = await env.request("messages.list", { folder_id: "f-inbox", limit: 3, offset: 0 }, 8);
  return list.ok && list.result.map((m) => m.id).join(",") === "1,2,3";
});
await scenario("unknown message is reported as not found", async () => {
  const env = makeEnv();
  return (await env.request("messages.get", { message_id: "9999" })).code === "not_found";
});
await scenario("oversized attachment is refused", async () => {
  const env = makeEnv({ attachmentFile: { size: 26 * 1024 * 1024, arrayBuffer: async () => new ArrayBuffer(1) } });
  return refused(await env.request("attachments.read", { message_id: "1", part_name: "1.2" }));
});

// Outgoing chunking: the response for a 3 MiB attachment must be split into parts of at most 768 KiB.
{
  const size = 3 * 1024 * 1024;
  const bytes = new Uint8Array(size).map((_, i) => i % 251);
  const env = makeEnv({ attachmentFile: { size, arrayBuffer: async () => bytes.buffer } });
  env.feed({ id: 41, op: "attachments.read", args: { message_id: "1", part_name: "1.2" } });
  await sleep(500);
  const parts = env.posted.slice();
  extra.outgoingParts = parts;
  const sizes = parts.map((p) => Buffer.byteLength(JSON.stringify(p), "utf8"));
  record("chunking: the large response is split into parts of at most 768 KiB",
    parts.length > 1 && parts.every((p) => "chunk" in p) && Math.max(...sizes) <= 768 * 1024, `${parts.length} parts`);
}

// Incoming chunking (made by the Python side): a draft body above 768 KiB arrives as several messages.
{
  const env = makeEnv();
  for (const part of input.chunkedDraftRequest) env.feed(part);
  await sleep(300);
  const begin = env.calls.find((c) => c[0] === "compose.beginNew");
  record("chunking: a split request is reassembled and handled",
    Boolean(begin) && begin[1].plainTextBody.length === input.chunkedDraftBodyLength, begin ? begin[1].plainTextBody.length : "no call");
}
await scenario("chunking: a malformed chunk is dropped without a write", async () => {
  const env = makeEnv();
  env.feed({ chunk: { id: "x", index: 5, total: 2 }, data: "AA==" });
  env.feed({ chunk: "bad", data: 5 });
  await sleep(50);
  const response = await env.request("health", {}, 3);
  return response.ok === true && env.writes().length === 0;
});

process.stdout.write(JSON.stringify({ scenarios, extra }));
