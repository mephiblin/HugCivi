(function attachHugCiviExtension(global) {
  const DEFAULT_SETTINGS = {
    serverUrl: "",
    username: "admin",
    password: "",
    targetSubdir: "",
    jobsLimit: 24
  };

  const ACTIVE_STATUSES = new Set(["queued", "running", "pausing", "canceling", "deleting"]);
  const FINISHED_STATUSES = new Set(["done", "failed", "paused", "canceled"]);

  class HugCiviError extends Error {
    constructor(message, options = {}) {
      super(message);
      this.name = "HugCiviError";
      this.status = options.status || 0;
      this.payload = options.payload || null;
    }
  }

  function storageGet(defaults) {
    return new Promise((resolve) => {
      chrome.storage.local.get(defaults, (items) => resolve(items || {}));
    });
  }

  function storageSet(values) {
    return new Promise((resolve) => {
      chrome.storage.local.set(values, () => resolve());
    });
  }

  async function getSettings() {
    const values = await storageGet(DEFAULT_SETTINGS);
    return normalizeSettings(values);
  }

  async function saveSettings(settings) {
    const normalized = normalizeSettings(settings);
    await storageSet(normalized);
    return normalized;
  }

  async function getLastActivity() {
    const values = await storageGet({lastActivity: null});
    return values.lastActivity || null;
  }

  async function saveLastActivity(activity) {
    const next = {
      at: new Date().toISOString(),
      ok: Boolean(activity.ok),
      message: String(activity.message || ""),
      inputText: String(activity.inputText || ""),
      jobIds: Array.isArray(activity.jobIds) ? activity.jobIds : []
    };
    await storageSet({lastActivity: next});
    return next;
  }

  function normalizeSettings(settings) {
    const merged = {...DEFAULT_SETTINGS, ...(settings || {})};
    return {
      serverUrl: normalizeServerUrl(merged.serverUrl),
      username: String(merged.username || "").trim() || "admin",
      password: String(merged.password || ""),
      targetSubdir: String(merged.targetSubdir || "").trim(),
      jobsLimit: clampInteger(merged.jobsLimit, 1, 100, DEFAULT_SETTINGS.jobsLimit)
    };
  }

  function normalizeServerUrl(value) {
    let text = String(value || "").trim();
    if (!text) return "";
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) {
      text = `http://${text}`;
    }
    text = text.replace(/\/+$/, "");
    return text;
  }

  function clampInteger(value, min, max, fallback) {
    const number = Number.parseInt(String(value), 10);
    if (!Number.isFinite(number)) return fallback;
    return Math.max(min, Math.min(max, number));
  }

  function assertReady(settings) {
    const normalized = normalizeSettings(settings);
    if (!normalized.serverUrl) {
      throw new HugCiviError("서버 주소를 입력하세요.");
    }
    let parsed;
    try {
      parsed = new URL(normalized.serverUrl);
    } catch (error) {
      throw new HugCiviError("서버 주소 형식이 올바르지 않습니다.", fromError(error));
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      throw new HugCiviError("서버 주소는 http 또는 https여야 합니다.");
    }
    if (!normalized.username || !normalized.password) {
      throw new HugCiviError("ID와 PW를 입력하세요.");
    }
    return normalized;
  }

  function fromError(error) {
    return {payload: {cause: error && error.message ? error.message : String(error)}};
  }

  function basicAuthHeader(username, password) {
    const bytes = new TextEncoder().encode(`${username}:${password}`);
    let binary = "";
    for (const byte of bytes) {
      binary += String.fromCharCode(byte);
    }
    return `Basic ${btoa(binary)}`;
  }

  function endpoint(settings, path) {
    return `${settings.serverUrl}${path}`;
  }

  async function requestJson(settings, path, options = {}) {
    const ready = assertReady(settings);
    const controller = new AbortController();
    const timeoutMs = clampInteger(options.timeoutMs || 15000, 1000, 120000, 15000);
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    headers.set("Authorization", basicAuthHeader(ready.username, ready.password));

    try {
      const response = await fetch(endpoint(ready, path), {
        method: options.method || "GET",
        body: options.body,
        headers,
        cache: "no-store",
        credentials: "omit",
        signal: controller.signal
      });
      const payload = await parseResponsePayload(response);
      if (!response.ok) {
        throw new HugCiviError(readErrorMessage(payload, response), {
          status: response.status,
          payload
        });
      }
      return payload;
    } catch (error) {
      if (error && error.name === "AbortError") {
        throw new HugCiviError("서버 응답 시간이 초과되었습니다.");
      }
      if (error instanceof HugCiviError) throw error;
      throw new HugCiviError(error && error.message ? error.message : "요청에 실패했습니다.", fromError(error));
    } finally {
      clearTimeout(timer);
    }
  }

  async function parseResponsePayload(response) {
    const text = await response.text();
    if (!text) return {};
    try {
      return JSON.parse(text);
    } catch (error) {
      return {detail: text};
    }
  }

  function readErrorMessage(payload, response) {
    if (payload && typeof payload.detail === "string") return payload.detail;
    if (payload && typeof payload.error === "string") return payload.error;
    if (response.status === 401) return "인증에 실패했습니다.";
    if (response.status === 503) return "서버가 설정을 완료하지 못했습니다.";
    return `HTTP ${response.status}`;
  }

  async function submitDownloadInput(inputText, settings, targetSubdir) {
    const value = String(inputText || "").trim();
    if (!value) {
      throw new HugCiviError("다운로드 입력이 비어 있습니다.");
    }
    const ready = assertReady(settings);
    const formData = new FormData();
    formData.append("input_text", value);
    formData.append("target_subdir", String(targetSubdir ?? ready.targetSubdir ?? "").trim());
    return requestJson(ready, "/api/jobs/bulk", {
      method: "POST",
      body: formData,
      timeoutMs: 30000
    });
  }

  async function fetchJobs(settings, limit) {
    const ready = assertReady(settings);
    const safeLimit = clampInteger(limit || ready.jobsLimit, 1, 100, ready.jobsLimit);
    const payload = await requestJson(ready, `/api/jobs?limit=${encodeURIComponent(String(safeLimit))}`);
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload.jobs)) return payload.jobs;
    return [];
  }

  function isHttpUrl(value) {
    try {
      const parsed = new URL(String(value || ""));
      return parsed.protocol === "http:" || parsed.protocol === "https:";
    } catch (error) {
      return false;
    }
  }

  function summarizeJobs(jobs) {
    const list = Array.isArray(jobs) ? jobs : [];
    const counts = {};
    for (const job of list) {
      const status = String(job.status || "unknown");
      counts[status] = (counts[status] || 0) + 1;
    }
    const activeJobs = list.filter((job) => ACTIVE_STATUSES.has(String(job.status || "")));
    const finishedJobs = list.filter((job) => FINISHED_STATUSES.has(String(job.status || "")));
    const byteKnown = activeJobs.filter((job) => numberValue(job.total_bytes) > 0);
    let overallPercent = null;
    if (byteKnown.length) {
      const total = byteKnown.reduce((sum, job) => sum + numberValue(job.total_bytes), 0);
      const progress = byteKnown.reduce((sum, job) => sum + numberValue(job.progress_bytes), 0);
      overallPercent = total > 0 ? clampPercent((progress / total) * 100) : null;
    } else {
      const percentKnown = activeJobs
        .map((job) => optionalNumber(job.percent))
        .filter((value) => value !== null);
      if (percentKnown.length) {
        const total = percentKnown.reduce((sum, value) => sum + value, 0);
        overallPercent = clampPercent(total / percentKnown.length);
      }
    }

    return {
      activeCount: activeJobs.length,
      finishedCount: finishedJobs.length,
      totalCount: list.length,
      counts,
      overallPercent
    };
  }

  function numberValue(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  function optionalNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function clampPercent(value) {
    if (!Number.isFinite(value)) return null;
    return Math.max(0, Math.min(100, Math.round(value * 10) / 10));
  }

  function shortInput(job) {
    return String(job.model_title || job.filename || job.input_text || job.source_url || "-").trim();
  }

  function jobProgress(job) {
    const pct = job.percent !== null && job.percent !== undefined ? Number(job.percent) : null;
    if (pct !== null && Number.isFinite(pct)) {
      const clamped = clampPercent(pct);
      const bytes = job.progress_human || job.total_human
        ? `${job.progress_human || "-"} / ${job.total_human || "-"}`
        : "";
      return {
        percent: clamped,
        label: bytes ? `${clamped}% · ${bytes}` : `${clamped}%`
      };
    }
    return {
      percent: null,
      label: String(job.progress_human || "")
    };
  }

  function createdJobIds(payload) {
    const created = payload && Array.isArray(payload.created) ? payload.created : [];
    return created
      .map((item) => item && item.job_id)
      .filter((value) => value !== null && value !== undefined);
  }

  function errorMessage(error) {
    if (error instanceof HugCiviError) return error.message;
    return error && error.message ? error.message : String(error || "알 수 없는 오류");
  }

  global.HugCiviExtension = {
    ACTIVE_STATUSES,
    DEFAULT_SETTINGS,
    HugCiviError,
    assertReady,
    createdJobIds,
    errorMessage,
    fetchJobs,
    getLastActivity,
    getSettings,
    isHttpUrl,
    jobProgress,
    normalizeServerUrl,
    normalizeSettings,
    saveLastActivity,
    saveSettings,
    shortInput,
    submitDownloadInput,
    summarizeJobs
  };
})(globalThis);
