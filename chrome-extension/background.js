importScripts("shared.js");

const HCE = globalThis.HugCiviExtension;
const BADGE_CLEAR_MS = 5000;

chrome.runtime.onInstalled.addListener(() => {
  chrome.action.setBadgeBackgroundColor({color: "#0b7f78"});
});

chrome.commands.onCommand.addListener((command) => {
  if (command === "send-current-url") {
    submitActiveTabUrl();
  }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || typeof message.type !== "string") return false;

  handleMessage(message)
    .then((payload) => sendResponse({ok: true, ...payload}))
    .catch((error) => sendResponse({ok: false, error: HCE.errorMessage(error)}));
  return true;
});

async function handleMessage(message) {
  if (message.type === "getActiveTabUrl") {
    const tab = await activeTab();
    return {url: tab.url || ""};
  }
  if (message.type === "submitActiveTab") {
    return submitActiveTabUrl({quiet: true});
  }
  if (message.type === "submitInput") {
    return submitInputText(String(message.inputText || ""));
  }
  if (message.type === "refreshJobs") {
    const settings = await HCE.getSettings();
    return {jobs: await HCE.fetchJobs(settings)};
  }
  if (message.type === "testConnection") {
    const settings = await HCE.getSettings();
    return {jobs: await HCE.fetchJobs(settings, 1)};
  }
  throw new HCE.HugCiviError("지원하지 않는 요청입니다.");
}

async function submitActiveTabUrl(options = {}) {
  try {
    const tab = await activeTab();
    const url = String(tab.url || "").trim();
    if (!HCE.isHttpUrl(url)) {
      throw new HCE.HugCiviError("현재 탭 URL을 보낼 수 없습니다.");
    }
    const result = await submitInputText(url);
    if (!options.quiet) {
      notify("HugCivi", "현재 탭 URL을 대기열에 추가했습니다.");
    }
    return result;
  } catch (error) {
    const message = HCE.errorMessage(error);
    await HCE.saveLastActivity({ok: false, message});
    flashBadge("ERR", "#b42318");
    if (!options.quiet) {
      notify("HugCivi", message);
    }
    throw error;
  }
}

async function submitInputText(inputText) {
  const settings = await HCE.getSettings();
  const payload = await HCE.submitDownloadInput(inputText, settings);
  const jobIds = HCE.createdJobIds(payload);
  const failed = Array.isArray(payload.failed) ? payload.failed : [];
  const message = failed.length
    ? `${payload.created_count || 0}개 추가, ${failed.length}개 실패`
    : `${payload.created_count || jobIds.length || 0}개를 대기열에 추가했습니다.`;
  const activity = await HCE.saveLastActivity({
    ok: failed.length === 0,
    message,
    inputText,
    jobIds
  });

  flashBadge(failed.length ? "WARN" : "OK", failed.length ? "#a15c00" : "#0b7f78");
  return {
    activity,
    submission: payload,
    jobs: Array.isArray(payload.jobs) ? payload.jobs : await HCE.fetchJobs(settings)
  };
}

function activeTab() {
  return new Promise((resolve, reject) => {
    chrome.tabs.query({active: true, currentWindow: true}, (tabs) => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new HCE.HugCiviError(error.message));
        return;
      }
      const tab = tabs && tabs[0];
      if (!tab) {
        reject(new HCE.HugCiviError("활성 탭을 찾지 못했습니다."));
        return;
      }
      resolve(tab);
    });
  });
}

function flashBadge(text, color) {
  chrome.action.setBadgeBackgroundColor({color});
  chrome.action.setBadgeText({text});
  setTimeout(() => {
    chrome.action.setBadgeText({text: ""});
  }, BADGE_CLEAR_MS);
}

function notify(title, message) {
  if (!chrome.notifications) return;
  chrome.notifications.create({
    type: "basic",
    iconUrl: "icons/hugcivi-128.png",
    title,
    message
  });
}
