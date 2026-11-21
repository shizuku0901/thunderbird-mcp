"use strict";

/*
 * Thunderbird MCP bridge: a thin relay.
 *
 * Decisions are made on the Python side (safety.py / service.py). This file only
 * relays a fixed table of operations to the messenger API and re-checks the same
 * rules with allowlists (J-1 .. J-7 in SPEC section 7). It adds no other logic.
 *
 * Mail-derived strings are returned as data and are never interpreted here.
 */

const HOST_NAME = "thunderbird_mcp_host";

// Native Messaging: one message must stay at or below 768 KiB, so larger ones are split.
const NM_PLAIN_LIMIT = 768 * 1024;
const NM_CHUNK_RAW_BYTES = 512 * 1024;
const MAX_LOGICAL_BYTES = 64 * 1024 * 1024;
const MAX_PENDING_ASSEMBLIES = 8;

// Limits that mirror safety.py.
const MAX_MESSAGES_PER_WRITE = 50; // J-5
const MAX_RECIPIENTS = 20;
const MAX_TAG_KEY_LEN = 64;
const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024;
const MAX_BODY_CHARS = 2000000;
const MAX_LIST_LIMIT = 50;

// J-3: allowlist. A folder with no special use, or only these, may receive messages.
const ALLOWED_MOVE_SPECIAL_USES = ["inbox", "archives"];

// J-2: the only keys ever passed to messages.update().
// (read, flagged and tags are each set through their own op below.)

class Refusal extends Error {}
class NotFound extends Error {}

// ---------------------------------------------------------------------------------------
// Fixed operation table (J-1)
// ---------------------------------------------------------------------------------------
const OPS = new Map();

function defineOp(name, handler) {
  OPS.set(name, handler);
}

// ---------------------------------------------------------------------------------------
// Validation helpers (same rules as safety.py, written as allowlists)
// ---------------------------------------------------------------------------------------
// Control characters, line separators and bidirectional overrides (same set as safety.py).
function hasControlChars(text) {
  for (const ch of text) {
    const c = ch.codePointAt(0);
    if (
      c < 0x20 ||
      (c >= 0x7f && c <= 0x9f) ||
      c === 0x2028 || c === 0x2029 ||
      (c >= 0x202a && c <= 0x202e) ||
      (c >= 0x2066 && c <= 0x2069)
    ) {
      return true;
    }
  }
  return false;
}
const ADDRESS_RE = /^[A-Za-z0-9.!#$%&'*+\/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$/;
const MESSAGE_ID_RE = /^[0-9]{1,15}$/;
const STANDARD_LABEL_RE = /^\$label[0-9]+$/;
const TAG_BAD_CHARS = /[\s\u0000-\u001f\u007f(){%*"\]\\]/;

function asObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

// J-5: 1 to 50 message ids, written as strings of digits, duplicates removed in order.
function parseMessageIds(value) {
  if (!Array.isArray(value)) throw new Refusal("message ids must be a list");
  const seen = new Set();
  const ids = [];
  for (const item of value) {
    if (typeof item !== "string" || !MESSAGE_ID_RE.test(item)) throw new Refusal("invalid message id");
    if (!seen.has(item)) {
      seen.add(item);
      ids.push(Number(item));
    }
  }
  if (ids.length === 0) throw new Refusal("no message ids given");
  if (ids.length > MAX_MESSAGES_PER_WRITE) throw new Refusal("too many messages in one write");
  return ids;
}

function parseId(value, what) {
  if (typeof value !== "string" || !MESSAGE_ID_RE.test(value)) throw new Refusal("invalid " + what);
  return Number(value);
}

function parseBool(value, what) {
  if (typeof value !== "boolean") throw new Refusal(what + " must be a boolean");
  return value;
}

function parseOptionalString(value, what) {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string" || hasControlChars(value)) throw new Refusal("invalid " + what);
  return value;
}

function clampInt(value, low, high, fallback) {
  if (value === null || value === undefined) return fallback;
  if (!Number.isInteger(value)) throw new Refusal("limit and offset must be integers");
  return Math.max(low, Math.min(high, value));
}

function checkAddresses(value, label) {
  if (!Array.isArray(value)) throw new Refusal(label + " must be a list of addresses");
  for (const address of value) {
    if (typeof address !== "string" || address.length > 254 || !ADDRESS_RE.test(address)) {
      throw new Refusal("invalid address in " + label);
    }
  }
  return value.slice();
}

// J-4: the key must be a known tag, well formed, and either a standard label or not start with "$".
function checkTagKeys(value, known) {
  if (!Array.isArray(value) || value.length === 0) throw new Refusal("tags must be a non-empty list");
  const keys = [];
  for (const key of value) {
    if (typeof key !== "string" || key.length === 0 || key.length > MAX_TAG_KEY_LEN) {
      throw new Refusal("invalid tag key");
    }
    if (key.startsWith("\\") || TAG_BAD_CHARS.test(key) || hasControlChars(key)) throw new Refusal("tag key has forbidden characters");
    if (key.startsWith("$") && !STANDARD_LABEL_RE.test(key)) throw new Refusal("tag key is not allowed");
    if (!known.has(key)) throw new Refusal("unknown tag key");
    if (!keys.includes(key)) keys.push(key);
  }
  return keys;
}

// A missing or empty list means "nothing to do"; anything else must pass checkTagKeys().
function optionalTagKeys(value, known) {
  if (value === undefined || value === null) return [];
  if (Array.isArray(value) && value.length === 0) return [];
  return checkTagKeys(value, known);
}

// ---------------------------------------------------------------------------------------
// messenger helpers
// ---------------------------------------------------------------------------------------
async function knownTagKeys() {
  const tags =
    messenger.messages.tags && typeof messenger.messages.tags.list === "function"
      ? await messenger.messages.tags.list()
      : await messenger.messages.listTags();
  return new Set(tags.map((tag) => tag.key));
}

async function listAllFolders(accountId) {
  if (messenger.folders && typeof messenger.folders.query === "function") {
    return messenger.folders.query(accountId ? { accountId } : {});
  }
  const accounts = await messenger.accounts.list(true);
  const out = [];
  const walk = (folders) => {
    for (const folder of folders || []) {
      out.push(folder);
      walk(folder.subFolders);
    }
  };
  for (const account of accounts) {
    if (!accountId || account.id === accountId) walk(account.folders);
  }
  return out;
}

async function getFolder(folderId) {
  if (typeof folderId !== "string" || folderId.length === 0 || hasControlChars(folderId)) {
    throw new Refusal("invalid folder id");
  }
  if (messenger.folders && typeof messenger.folders.get === "function") {
    try {
      return await messenger.folders.get(folderId);
    } catch (error) {
      throw new NotFound("folder");
    }
  }
  const folder = (await listAllFolders(null)).find((f) => f.id === folderId);
  if (!folder) throw new NotFound("folder");
  return folder;
}

async function getHeader(id) {
  let header = null;
  try {
    header = await messenger.messages.get(id);
  } catch (error) {
    header = null;
  }
  if (!header) throw new NotFound("message");
  return header;
}

function mapFolder(folder) {
  return {
    id: String(folder.id),
    account_id: String(folder.accountId),
    name: String(folder.name),
    path: String(folder.path),
    special_use: Array.isArray(folder.specialUse) ? folder.specialUse.map(String) : [],
  };
}

function mapSummary(header, hasAttachments) {
  return {
    id: String(header.id),
    folder_id: header.folder && header.folder.id !== undefined ? String(header.folder.id) : "",
    subject: header.subject == null ? "" : String(header.subject),
    author: header.author == null ? "" : String(header.author),
    date: header.date ? new Date(header.date).toISOString() : "",
    read: Boolean(header.read),
    flagged: Boolean(header.flagged),
    tags: Array.isArray(header.tags) ? header.tags.map(String) : [],
    // List results do not carry this; get_message fills it in from the attachment list.
    has_attachments: Boolean(hasAttachments),
  };
}

function mapAttachment(attachment) {
  return {
    part_name: String(attachment.partName),
    filename: attachment.name == null ? "" : String(attachment.name),
    content_type: attachment.contentType == null ? "" : String(attachment.contentType),
    size: Number.isInteger(attachment.size) ? attachment.size : 0,
  };
}

// Collect up to `limit` messages from a paged MessageList, skipping `offset` first.
async function collectMessages(list, limit, offset) {
  const out = [];
  let skipped = 0;
  for (let page = 0; page < 2000 && list; page++) {
    for (const message of list.messages || []) {
      if (skipped < offset) {
        skipped++;
        continue;
      }
      out.push(message);
      if (out.length >= limit) {
        if (list.id && typeof messenger.messages.abortList === "function") {
          await messenger.messages.abortList(list.id).catch(() => {});
        }
        return out;
      }
    }
    if (!list.id) break;
    list = await messenger.messages.continueList(list.id);
  }
  return out;
}

function extractText(part) {
  const plain = [];
  const html = [];
  const walk = (node) => {
    if (!node) return;
    const type = String(node.contentType || "").toLowerCase();
    if (typeof node.body === "string" && !node.name) {
      if (type === "text/plain") plain.push(node.body);
      else if (type === "text/html") html.push(node.body);
    }
    for (const child of node.parts || []) walk(child);
  };
  walk(part);
  let text = plain.length > 0 ? plain.join("\n") : html.join("\n").replace(/<[^>]*>/g, "");
  if (text.length > MAX_BODY_CHARS) text = text.slice(0, MAX_BODY_CHARS);
  return text;
}

function bytesToBase64(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

function base64ToBytes(text) {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

// ---------------------------------------------------------------------------------------
// Operations (fixed table, SPEC section 7)
// ---------------------------------------------------------------------------------------
defineOp("accounts.list", async () => {
  const accounts = await messenger.accounts.list(false);
  return accounts.map((a) => ({ id: String(a.id), name: String(a.name), type: String(a.type) }));
});

defineOp("folders.list", async (args) => {
  const accountId = parseOptionalString(args.account_id, "account id");
  return (await listAllFolders(accountId)).map(mapFolder);
});

defineOp("messages.query", async (args) => {
  const limit = clampInt(args.limit, 1, MAX_LIST_LIMIT, 20);
  const query = {};
  if (typeof args.text === "string" && args.text.length > 0) query.subject = args.text;
  const folderId = parseOptionalString(args.folder_id, "folder id");
  if (folderId) query.folderId = folderId;
  if (args.unread_only === true) query.unread = true;
  if (args.flagged_only === true) query.flagged = true;
  const tag = parseOptionalString(args.tag, "tag");
  if (tag) query.tags = { tags: { [tag]: true }, mode: "all" };
  const messages = await collectMessages(await messenger.messages.query(query), limit, 0);
  return messages.map((header) => mapSummary(header, false));
});

defineOp("messages.list", async (args) => {
  const limit = clampInt(args.limit, 1, MAX_LIST_LIMIT, 20);
  const offset = clampInt(args.offset, 0, 1000000, 0);
  const folder = await getFolder(args.folder_id);
  const messages = await collectMessages(await messenger.messages.list(folder.id), limit, offset);
  return messages.map((header) => mapSummary(header, false));
});

defineOp("messages.get", async (args) => {
  const id = parseId(args.message_id, "message id");
  const header = await getHeader(id);
  const full = await messenger.messages.getFull(id);
  const attachments = await messenger.messages.listAttachments(id);
  return {
    summary: mapSummary(header, attachments.length > 0),
    body_text: extractText(full),
    attachments: attachments.map(mapAttachment),
  };
});

defineOp("attachments.list", async (args) => {
  const id = parseId(args.message_id, "message id");
  await getHeader(id);
  return (await messenger.messages.listAttachments(id)).map(mapAttachment);
});

defineOp("attachments.read", async (args) => {
  const id = parseId(args.message_id, "message id");
  if (typeof args.part_name !== "string" || args.part_name.length === 0 || hasControlChars(args.part_name)) {
    throw new Refusal("invalid part name");
  }
  await getHeader(id);
  const file = await messenger.messages.getAttachmentFile(id, args.part_name);
  if (!file) throw new NotFound("attachment");
  if (file.size > MAX_ATTACHMENT_BYTES) throw new Refusal("attachment is too large");
  const bytes = new Uint8Array(await file.arrayBuffer());
  return { data_b64: bytesToBase64(bytes) };
});

// J-2: each update below passes exactly one of the keys read, flagged or tags.
defineOp("messages.mark", async (args) => {
  const ids = parseMessageIds(args.message_ids);
  const read = parseBool(args.read, "read");
  for (const id of ids) await messenger.messages.update(id, { read });
  return null;
});

defineOp("messages.flag", async (args) => {
  const ids = parseMessageIds(args.message_ids);
  const flagged = parseBool(args.flagged, "flagged");
  for (const id of ids) await messenger.messages.update(id, { flagged });
  return null;
});

defineOp("tags.update", async (args) => {
  const ids = parseMessageIds(args.message_ids);
  const known = await knownTagKeys();
  const additions = optionalTagKeys(args.add, known);
  const removals = optionalTagKeys(args.remove, known);
  if (additions.length === 0 && removals.length === 0) throw new Refusal("no tags given");
  for (const id of ids) {
    const header = await getHeader(id);
    const current = Array.isArray(header.tags) ? header.tags : [];
    const next = current.filter((key) => !removals.includes(key));
    for (const key of additions) if (!next.includes(key)) next.push(key);
    await messenger.messages.update(id, { tags: next });
  }
  return null;
});

defineOp("tags.list", async () => {
  const tags =
    messenger.messages.tags && typeof messenger.messages.tags.list === "function"
      ? await messenger.messages.tags.list()
      : await messenger.messages.listTags();
  return tags.map((tag) => ({
    key: String(tag.key),
    name: String(tag.tag === undefined ? tag.key : tag.tag),
    color: tag.color == null ? "" : String(tag.color),
  }));
});

defineOp("messages.move", async (args) => {
  const ids = parseMessageIds(args.message_ids);
  const destination = await getFolder(args.destination_folder_id);
  const specialUse = Array.isArray(destination.specialUse) ? destination.specialUse : [];
  // J-3: allowlist on the special use of the destination.
  if (!specialUse.every((use) => ALLOWED_MOVE_SPECIAL_USES.includes(use))) {
    throw new Refusal("destination folder is not allowed");
  }
  await messenger.messages.move(ids, destination.id);
  return null;
});

// J-6: the compose window must be closed after saving; if it cannot be, report an error.
async function closeComposeWindow(tab) {
  try {
    await messenger.tabs.remove(tab.id);
    return;
  } catch (error) {
    // fall through to closing the whole window
  }
  try {
    await messenger.windows.remove(tab.windowId);
    return;
  } catch (error) {
    throw new Error("could not close the compose window");
  }
}

defineOp("drafts.create", async (args) => {
  const to = checkAddresses(args.to, "to");
  const cc = checkAddresses(args.cc === undefined || args.cc === null ? [] : args.cc, "cc");
  const bcc = checkAddresses(args.bcc === undefined || args.bcc === null ? [] : args.bcc, "bcc");
  if (to.length === 0) throw new Refusal("at least one recipient is required");
  if (to.length + cc.length + bcc.length > MAX_RECIPIENTS) throw new Refusal("too many recipients");
  if (typeof args.subject !== "string" || hasControlChars(args.subject)) throw new Refusal("invalid subject");
  if (typeof args.body !== "string" || args.body.includes("\u0000")) throw new Refusal("invalid body");
  const identityId = parseOptionalString(args.identity_id, "identity id");
  if (identityId === "") throw new Refusal("invalid identity id");

  const details = { to, cc, bcc, subject: args.subject, plainTextBody: args.body, isPlainText: true };
  if (identityId) details.identityId = identityId;

  const tab = await messenger.compose.beginNew(details);
  let saved;
  try {
    // The only call that stores the draft: mode "draft".
    saved = await messenger.compose.saveMessage(tab.id, { mode: "draft" });
  } catch (error) {
    await closeComposeWindow(tab).catch(() => {});
    throw error;
  }
  await closeComposeWindow(tab);
  const header = saved && Array.isArray(saved.messages) ? saved.messages[0] : null;
  if (!header) throw new Error("the draft was saved but its id is unknown");
  return {
    message_id: String(header.id),
    folder_id: header.folder && header.folder.id !== undefined ? String(header.folder.id) : "",
  };
});

// J-7: report how drafts are created. messages.saveMessage() is not used because its
// signature is unverified (SPEC section 12); drafts always go through a compose window.
function detectDraftMode() {
  return "compose_window";
}
const DRAFT_MODE = detectDraftMode();

defineOp("health", async () => {
  const info = await messenger.runtime.getBrowserInfo();
  return { thunderbird_version: String(info.version), draft_mode: DRAFT_MODE };
});

// ---------------------------------------------------------------------------------------
// Request handling
// ---------------------------------------------------------------------------------------
function failure(id, error) {
  if (error instanceof Refusal) return { id, ok: false, code: "refused", error: "refused: " + error.message };
  if (error instanceof NotFound) return { id, ok: false, code: "not_found", error: "not found" };
  const text = error && error.message ? String(error.message).slice(0, 200) : "error";
  return { id, ok: false, error: text };
}

async function handleRequest(request) {
  if (!asObject(request)) return failure(null, new Refusal("malformed request"));
  const id = request.id;
  try {
    const handler = typeof request.op === "string" ? OPS.get(request.op) : undefined;
    if (!handler) throw new Refusal("unknown op"); // J-1
    if (!asObject(request.args)) throw new Refusal("malformed arguments");
    return { id, ok: true, result: await handler(request.args) };
  } catch (error) {
    return failure(id, error);
  }
}

// ---------------------------------------------------------------------------------------
// Native Messaging transport (chunks of at most 768 KiB)
// ---------------------------------------------------------------------------------------
function randomId() {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function splitOutgoing(object) {
  const bytes = new TextEncoder().encode(JSON.stringify(object));
  if (bytes.length <= NM_PLAIN_LIMIT) return [object];
  const total = Math.ceil(bytes.length / NM_CHUNK_RAW_BYTES);
  const id = randomId();
  const parts = [];
  for (let index = 0; index < total; index++) {
    const slice = bytes.subarray(index * NM_CHUNK_RAW_BYTES, (index + 1) * NM_CHUNK_RAW_BYTES);
    parts.push({ chunk: { id, index, total }, data: bytesToBase64(slice) });
  }
  return parts;
}

const pending = new Map(); // chunk id -> { total, parts: Map(index -> Uint8Array), size }

// Returns the whole message, or null while chunks are still missing.
function assembleIncoming(message) {
  if (!asObject(message) || !("chunk" in message)) return message;
  const info = message.chunk;
  if (!asObject(info) || typeof message.data !== "string") throw new Error("malformed chunk");
  const { id, index, total } = info;
  if (
    typeof id !== "string" || id.length === 0 || id.length > 64 ||
    !Number.isInteger(index) || !Number.isInteger(total) ||
    total <= 0 || total > 4096 || index < 0 || index >= total
  ) {
    throw new Error("malformed chunk header");
  }
  if (!pending.has(id)) {
    if (pending.size >= MAX_PENDING_ASSEMBLIES) throw new Error("too many unfinished messages");
    pending.set(id, { total, parts: new Map(), size: 0 });
  }
  const entry = pending.get(id);
  if (entry.total !== total || entry.parts.has(index)) throw new Error("inconsistent chunk");
  const bytes = base64ToBytes(message.data);
  entry.parts.set(index, bytes);
  entry.size += bytes.length;
  if (entry.size > MAX_LOGICAL_BYTES) {
    pending.clear();
    throw new Error("message is too large");
  }
  if (entry.parts.size < total) return null;
  const joined = new Uint8Array(entry.size);
  let offset = 0;
  for (let i = 0; i < total; i++) {
    joined.set(entry.parts.get(i), offset);
    offset += entry.parts.get(i).length;
  }
  pending.clear();
  return JSON.parse(new TextDecoder().decode(joined));
}

let port = null;
let retryMs = 1000;
let queue = Promise.resolve(); // requests are handled one at a time

function post(response) {
  if (!port) return;
  for (const part of splitOutgoing(response)) port.postMessage(part);
}

function onNativeMessage(message) {
  retryMs = 1000;
  let request;
  try {
    request = assembleIncoming(message);
  } catch (error) {
    console.error("thunderbird-mcp: dropped a malformed message: " + error.message);
    pending.clear();
    return;
  }
  if (request === null) return;
  queue = queue
    .then(() => handleRequest(request))
    .then(post)
    .catch((error) => console.error("thunderbird-mcp: request failed: " + (error && error.message)));
}

function connect() {
  port = messenger.runtime.connectNative(HOST_NAME);
  port.onMessage.addListener(onNativeMessage);
  port.onDisconnect.addListener(() => {
    port = null;
    pending.clear();
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 60000);
  });
}

connect();
