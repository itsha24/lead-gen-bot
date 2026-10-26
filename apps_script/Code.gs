/**
 * Job-alert reader for Lead Gen Bot.
 *
 * Runs in YOUR Gmail account (script.google.com) as a small web app. When the
 * daily GitHub run asks for it, it returns the emails under the "job-alerts"
 * label from the last few days, and nothing else. Read-only: it can't send,
 * change or delete mail (see appsscript.json: gmail.readonly).
 *
 * Setup: see apps_script/README.md.
 */
const LABEL = "job-alerts";
const DAYS = 3;
const MAX_MESSAGES = 40;

function doGet(e) {
  const key = PropertiesService.getScriptProperties().getProperty("KEY");
  if (!key || !e || !e.parameter || e.parameter.key !== key) return json_({ error: "wrong or missing key" });
  const list = Gmail.Users.Messages.list("me", { q: `label:${LABEL} newer_than:${DAYS}d`, maxResults: MAX_MESSAGES });
  const messages = (list.messages || []).map(({ id }) => {
    const m = Gmail.Users.Messages.get("me", id, { format: "full" });
    const header = name => ((m.payload.headers || []).find(h => h.name.toLowerCase() === name) || {}).value || "";
    const html = findBody_(m.payload, "text/html") || findBody_(m.payload, "text/plain") || "";
    return {
      id,
      from: header("from"),
      subject: header("subject"),
      date: new Date(Number(m.internalDate)).toISOString(),
      html: html.replace(/<style[\s\S]*?<\/style>/gi, "").slice(0, 300000),
    };
  });
  return json_({ messages });
}

function findBody_(part, type) {
  if (part.mimeType === type && part.body && part.body.data)
    return Utilities.newBlob(Utilities.base64DecodeWebSafe(part.body.data)).getDataAsString("UTF-8");
  for (const p of part.parts || []) {
    const found = findBody_(p, type);
    if (found) return found;
  }
  return "";
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

/** Run this once: it makes the secret key and shows how many alert emails it can see. */
function setup() {
  const props = PropertiesService.getScriptProperties();
  if (!props.getProperty("KEY")) props.setProperty("KEY", Utilities.getUuid());
  const n = (Gmail.Users.Messages.list("me", { q: `label:${LABEL} newer_than:${DAYS}d` }).messages || []).length;
  Logger.log(`Key: ${props.getProperty("KEY")}`);
  Logger.log(`Alert emails in "${LABEL}" from the last ${DAYS} days: ${n}`);
}
