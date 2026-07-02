const HCE = globalThis.HugCiviExtension;

const elements = {
  serverUrl: document.getElementById("server-url"),
  username: document.getElementById("username"),
  password: document.getElementById("password"),
  targetSubdir: document.getElementById("target-subdir"),
  saveSettings: document.getElementById("save-settings"),
  testConnection: document.getElementById("test-connection"),
  openShortcuts: document.getElementById("open-shortcuts"),
  openServer: document.getElementById("open-server"),
  downloadInput: document.getElementById("download-input"),
  useCurrentTab: document.getElementById("use-current-tab"),
  sendCurrentTab: document.getElementById("send-current-tab"),
  submitInput: document.getElementById("submit-input"),
  refreshJobs: document.getElementById("refresh-jobs"),
  overallLabel: document.getElementById("overall-label"),
  overallPercent: document.getElementById("overall-percent"),
  overallBar: document.getElementById("overall-bar"),
  jobCounts: document.getElementById("job-counts"),
  jobsList: document.getElementById("jobs-list"),
  activity: document.getElementById("activity"),
  status: document.getElementById("status")
};

let currentSettings = HCE.normalizeSettings({});
let refreshTimer = null;

document.addEventListener("DOMContentLoaded", init);

async function init() {
  currentSettings = await HCE.getSettings();
  populateSettings(currentSettings);
  bindEvents();
  await renderLastActivity();
  await refreshJobs({silent: true});
  refreshTimer = setInterval(() => refreshJobs({silent: true}), 2500);
}

function bindEvents() {
  elements.saveSettings.addEventListener("click", () => saveSettings());
  elements.testConnection.addEventListener("click", testConnection);
  elements.openShortcuts.addEventListener("click", () => openUrl("chrome://extensions/shortcuts"));
  elements.openServer.addEventListener("click", openServer);
  elements.useCurrentTab.addEventListener("click", useCurrentTab);
  elements.sendCurrentTab.addEventListener("click", sendCurrentTab);
  elements.submitInput.addEventListener("click", submitInput);
  elements.refreshJobs.addEventListener("click", () => refreshJobs({silent: false}));
  elements.downloadInput.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      submitInput();
    }
  });
  window.addEventListener("unload", () => {
    if (refreshTimer) clearInterval(refreshTimer);
  });
}

function populateSettings(settings) {
  elements.serverUrl.value = settings.serverUrl;
  elements.username.value = settings.username;
  elements.password.value = settings.password;
  elements.targetSubdir.value = settings.targetSubdir;
}

function collectSettings() {
  return HCE.normalizeSettings({
    serverUrl: elements.serverUrl.value,
    username: elements.username.value,
    password: elements.password.value,
    targetSubdir: elements.targetSubdir.value,
    jobsLimit: currentSettings.jobsLimit
  });
}

async function saveSettings(options = {}) {
  currentSettings = await HCE.saveSettings(collectSettings());
  populateSettings(currentSettings);
  if (!options.silent) setStatus("저장했습니다.");
  return currentSettings;
}

async function testConnection() {
  return withBusy([elements.testConnection], async () => {
    try {
      currentSettings = await HCE.saveSettings(collectSettings());
      populateSettings(currentSettings);
      const jobs = await HCE.fetchJobs(currentSettings, 1);
      renderJobs(jobs);
      setStatus("연결됐습니다.");
    } catch (error) {
      setStatus(HCE.errorMessage(error), true);
    }
  });
}

async function useCurrentTab() {
  return withBusy([elements.useCurrentTab], async () => {
    try {
      const response = await sendMessage({type: "getActiveTabUrl"});
      if (!HCE.isHttpUrl(response.url)) {
        setStatus("현재 탭 URL을 사용할 수 없습니다.", true);
        return;
      }
      elements.downloadInput.value = response.url;
      setStatus("현재 탭 URL을 가져왔습니다.");
    } catch (error) {
      setStatus(HCE.errorMessage(error), true);
    }
  });
}

async function sendCurrentTab() {
  return withBusy([elements.sendCurrentTab, elements.submitInput], async () => {
    try {
      await saveSettings({silent: true});
      const response = await sendMessage({type: "submitActiveTab"});
      afterSubmission(response);
    } catch (error) {
      setStatus(HCE.errorMessage(error), true);
      await renderLastActivity();
    }
  });
}

async function submitInput() {
  return withBusy([elements.submitInput, elements.sendCurrentTab], async () => {
    try {
      await saveSettings({silent: true});
      const inputText = elements.downloadInput.value.trim();
      const response = await sendMessage({type: "submitInput", inputText});
      elements.downloadInput.value = "";
      afterSubmission(response);
    } catch (error) {
      setStatus(HCE.errorMessage(error), true);
      await renderLastActivity();
    }
  });
}

function afterSubmission(response) {
  const activity = response.activity || null;
  if (Array.isArray(response.jobs)) {
    renderJobs(response.jobs);
  }
  renderActivity(activity);
  setStatus(activity && activity.message ? activity.message : "요청했습니다.", activity && !activity.ok);
}

async function refreshJobs(options = {}) {
  try {
    const settings = collectSettings();
    if (!settings.serverUrl || !settings.password) return;
    const jobs = await HCE.fetchJobs(settings);
    renderJobs(jobs);
    if (!options.silent) setStatus("새로고침했습니다.");
  } catch (error) {
    if (!options.silent) setStatus(HCE.errorMessage(error), true);
  }
}

function renderJobs(jobs) {
  const list = Array.isArray(jobs) ? jobs : [];
  renderOverview(list);
  if (!list.length) {
    elements.jobsList.innerHTML = '<div class="empty">작업이 없습니다.</div>';
    return;
  }
  elements.jobsList.innerHTML = list.slice(0, 8).map(renderJob).join("");
}

function renderOverview(jobs) {
  const summary = HCE.summarizeJobs(jobs);
  if (summary.activeCount) {
    elements.overallLabel.textContent = `${summary.activeCount}개 진행 중`;
  } else {
    elements.overallLabel.textContent = "진행 중인 작업 없음";
  }

  const percent = summary.overallPercent;
  elements.overallBar.classList.toggle("indeterminate", summary.activeCount > 0 && percent === null);
  if (percent !== null) {
    elements.overallPercent.textContent = `${percent}%`;
    elements.overallBar.querySelector("span").style.width = `${percent}%`;
  } else {
    elements.overallPercent.textContent = summary.activeCount ? "계산 중" : "-";
    elements.overallBar.querySelector("span").style.width = summary.activeCount ? "42%" : "0";
  }

  const order = ["running", "queued", "done", "failed", "paused", "canceled"];
  elements.jobCounts.innerHTML = order
    .filter((status) => summary.counts[status])
    .map((status) => `<span class="count-pill">${escapeHtml(status)} ${summary.counts[status]}</span>`)
    .join("");
}

function renderJob(job) {
  const progress = HCE.jobProgress(job);
  const width = progress.percent !== null ? `${progress.percent}%` : "0";
  return `
    <article class="job-row">
      <div class="job-top">
        <span class="job-title">${escapeHtml(HCE.shortInput(job))}</span>
        <span class="job-status ${escapeHtml(job.status || "")}">${escapeHtml(job.status || "-")}</span>
      </div>
      <div class="progress-bar${progress.percent === null ? " indeterminate" : ""}">
        <span style="width:${escapeHtml(width)}"></span>
      </div>
      <div class="job-meta">
        <span>#${escapeHtml(job.id || "-")} · ${escapeHtml(job.source || "-")}</span>
        <span>${escapeHtml(progress.label || "")}</span>
      </div>
    </article>
  `;
}

async function renderLastActivity() {
  renderActivity(await HCE.getLastActivity());
}

function renderActivity(activity) {
  if (!activity || !activity.message) {
    elements.activity.hidden = true;
    elements.activity.textContent = "";
    return;
  }
  elements.activity.hidden = false;
  elements.activity.classList.toggle("error", !activity.ok);
  elements.activity.textContent = activity.message;
}

function openServer() {
  const settings = collectSettings();
  if (!settings.serverUrl) {
    setStatus("서버 주소를 입력하세요.", true);
    return;
  }
  openUrl(settings.serverUrl);
}

function openUrl(url) {
  chrome.tabs.create({url}, () => {
    const error = chrome.runtime.lastError;
    if (error) setStatus(error.message, true);
  });
}

function sendMessage(message) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(message, (response) => {
      const runtimeError = chrome.runtime.lastError;
      if (runtimeError) {
        reject(new Error(runtimeError.message));
        return;
      }
      if (!response || response.ok !== true) {
        reject(new Error(response && response.error ? response.error : "요청에 실패했습니다."));
        return;
      }
      resolve(response);
    });
  });
}

async function withBusy(buttons, task) {
  buttons.forEach((button) => {
    button.disabled = true;
  });
  try {
    await task();
  } finally {
    buttons.forEach((button) => {
      button.disabled = false;
    });
  }
}

function setStatus(message, isError = false) {
  elements.status.textContent = message || "";
  elements.status.classList.toggle("error", Boolean(isError));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
