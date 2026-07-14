const TESTS = Array.from({ length: 13 }, (_, index) => {
  const setNumber = index + 1;
  return [
    { id: `local_celpip${setNumber}_test1`, label: `CELPIP ${setNumber} - Test 1` },
    { id: `local_celpip${setNumber}_test2`, label: `CELPIP ${setNumber} - Test 2` },
  ];
}).flat();

const MATERIAL_ROOT = "../materials/private/packs";
const SERVER_API_ENABLED = true;

const SECTIONS = [
  { id: "listening", label: "Listening", minutes: 47 },
  { id: "reading", label: "Reading", minutes: 55 },
  { id: "writing", label: "Writing", minutes: 53 },
  { id: "speaking", label: "Speaking", minutes: 20 },
];

const LISTENING_PART_LABELS = ["1A", "1B", "1C", "2", "3", "4", "5", "6"];
const LISTENING_GROUP_TIMERS = {
  "4": 210,
  "5": 240,
  "6": 260,
};

const SCORE_TABLES = {
  listening: [
    { level: "10-12", min: 35, max: 38 },
    { level: "9", min: 33, max: 35 },
    { level: "8", min: 30, max: 33 },
    { level: "7", min: 27, max: 31 },
    { level: "6", min: 22, max: 28 },
    { level: "5", min: 17, max: 23 },
    { level: "4", min: 11, max: 18 },
    { level: "3", min: 7, max: 12 },
    { level: "M", min: 0, max: 7 },
  ],
  reading: [
    { level: "10-12", min: 33, max: 38 },
    { level: "9", min: 31, max: 33 },
    { level: "8", min: 28, max: 31 },
    { level: "7", min: 24, max: 28 },
    { level: "6", min: 19, max: 25 },
    { level: "5", min: 15, max: 20 },
    { level: "4", min: 10, max: 16 },
    { level: "3", min: 8, max: 11 },
    { level: "M", min: 0, max: 7 },
  ],
};

const state = {
  testId: TESTS[0].id,
  data: null,
  section: "listening",
  index: 0,
  answers: {},
  checked: {},
  submissions: {},
  timings: {},
  notes: {},
  listeningUnlocked: new Set(),
  listeningQuestionIndex: new Map(),
  listeningQuestionTimer: null,
  sourceCache: new Map(),
  dbStatus: {},
  draftSyncTimers: new Map(),
  sectionIntro: false,
  submittingSection: false,
  timer: {
    id: null,
    remaining: 0,
    elapsed: 0,
    running: false,
  },
};

const $ = (id) => document.getElementById(id);

function storageKey(testId = state.testId) {
  return `celpip-practice:${testId}`;
}

function checkedStorageKey(testId = state.testId) {
  return `${storageKey(testId)}:checked`;
}

function submissionStorageKey(testId = state.testId) {
  return `${storageKey(testId)}:submissions`;
}

function timingStorageKey(testId = state.testId) {
  return `${storageKey(testId)}:timings`;
}

function notesStorageKey(testId = state.testId) {
  return `${storageKey(testId)}:notes`;
}

function assetUrl(path) {
  if (!path) return "";
  return materialUrl(state.testId, path);
}

function sourceUrl(path) {
  return materialUrl(state.testId, path);
}

function materialUrl(testId, path) {
  return `${MATERIAL_ROOT}/${testId}/${path}`;
}

function mergeAnswers(databaseAnswers = {}, localAnswers = {}) {
  const answers = { ...databaseAnswers };
  for (const [key, value] of Object.entries(localAnswers)) {
    if (value !== "") answers[key] = value;
  }
  return answers;
}

function mergeSubmissions(databaseSubmissions = {}, localSubmissions = {}) {
  const submissions = { ...localSubmissions };
  for (const [section, submission] of Object.entries(databaseSubmissions || {})) {
    if (submission) submissions[section] = submission;
  }
  return submissions;
}

function sectionQuestions() {
  return (state.data?.questions || []).filter((q) => q.section === state.section);
}

function sectionGroups() {
  const questionsByKey = state.data?.questions_by_key
    || Object.fromEntries((state.data?.questions || []).map((question) => [question.key, question]));
  const jsonGroups = state.data?.question_groups?.[state.section];
  if (jsonGroups?.length) {
    return jsonGroups.map((group) => ({
      ...group,
      page: group.source_file,
      questions: group.question_keys.map((key) => questionsByKey[key]).filter(Boolean),
    }));
  }

  const groups = [];
  const byPage = new Map();
  for (const q of sectionQuestions()) {
    const page = q.source_pages?.[0]?.file || q.source_file || q.key;
    if (!byPage.has(page)) {
      const group = {
        page,
        source_file: page,
        title: pageTitle(q),
        media: [],
        questions: [],
      };
      byPage.set(page, group);
      groups.push(group);
    }
    byPage.get(page).questions.push(q);
  }
  return groups;
}

function currentGroup() {
  return sectionGroups()[state.index];
}

function pageTitle(q) {
  return q?.source_pages?.[0]?.title || q?.source_file?.split("/").pop()?.replace(/[-_]/g, " ") || state.section;
}

function displayGroupTitle(group, index = state.index) {
  const fallback = `${state.section} part ${index + 1}`;
  const rawTitle = String(group?.title || group?.source_file || group?.page || fallback)
    .replace(/\.[a-z0-9]+$/i, "")
    .replace(/[-_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const withoutPrefix = rawTitle.replace(/^\d+\s+/, "");
  const normalized = withoutPrefix.replace(/\bsection\s*(\d+)\b/i, "Section $1");
  return normalized.replace(/\b[a-z]/g, (char) => char.toUpperCase());
}

function questionNumber(q, i = state.index) {
  return q?.number || i + 1;
}

async function init() {
  $("prevBtn").addEventListener("click", () => moveQuestion(-1));
  $("nextBtn").addEventListener("click", handleNext);
  $("submitSectionBtn").addEventListener("click", submitSection);
  $("overviewBtn").addEventListener("click", showOverview);
  $("historyBtn").addEventListener("click", showHistory);
  $("timerBtn").addEventListener("click", toggleTimer);
  $("toggleSourceBtn").addEventListener("click", toggleSource);
  const requestedView = new URLSearchParams(window.location.search).get("view");
  if (requestedView === "history") {
    await showHistory();
    return;
  }
  const route = readUrlRoute();
  if (route) {
    state.testId = route.testId;
    state.section = route.section;
    state.index = route.partIndex;
    state.sectionIntro = route.sectionIntro;
    await loadTest();
  } else {
    await showOverview();
  }
}

async function loadTest() {
  setView("practice");
  const response = await fetch(sourceUrl("questions.json"));
  state.data = await response.json();
  const localDraft = readLocalDraft(state.testId);
  const databaseDraft = await fetchDatabaseDraft(state.testId);
  state.answers = mergeAnswers(databaseDraft?.answers, localDraft.answers);
  state.checked = { ...(databaseDraft?.checked || {}), ...localDraft.checked };
  state.submissions = mergeSubmissions(databaseDraft?.submissions, localDraft.submissions);
  state.timings = { ...(databaseDraft?.timings || {}), ...localDraft.timings };
  state.notes = { ...(databaseDraft?.notes || {}), ...localDraft.notes };
  await restoreListeningReviewFromHistory();
  persist({ sync: false });
  scheduleDraftSync(state.testId, 0);
  renderSections();
  resetSectionTimer();
  await render();
  if (state.section === "reading" && !state.submissions[state.section] && !state.timer.running) toggleTimer();
}

async function fetchDatabaseDraft(testId) {
  if (!SERVER_API_ENABLED) return null;
  try {
    const response = await fetch("/api/drafts");
    if (!response.ok) return null;
    const drafts = (await response.json()).drafts || [];
    return drafts.find((draft) => draft.test_id === testId) || null;
  } catch {
    return null;
  }
}

async function restoreListeningReviewFromHistory() {
  if (state.section !== "listening" || state.submissions.listening) return false;
  const questions = sectionChoiceQuestions();
  if (!questions.length || !questions.every((question) => state.answers[question.key])) return false;

  let restoredCorrect = 0;
  questions.forEach((question) => {
    const selected = state.answers[question.key];
    const option = question.options.find((item) => item.id === selected || item.value === selected);
    const isCorrect = Boolean(option?.is_correct);
    state.checked[question.key] = isCorrect;
    if (isCorrect) restoredCorrect += 1;
  });
  const level = estimateLevel("listening", restoredCorrect, questions.length);
  state.submissions.listening = {
    total: questions.length,
    correct: restoredCorrect,
    level: level?.level || null,
    elapsed_seconds: state.timings.listening?.elapsed_seconds ?? null,
    note: level
      ? `Practice estimate using the published raw-score range ${level.min}-${level.max}. Official scores can vary by test form.`
      : "Recovered from saved practice history.",
    submitted_at: new Date().toISOString(),
    restored_from_history: true,
  };
  return true;
}

function setView(view) {
  $("overviewView").hidden = view !== "overview";
  $("historyView").hidden = view !== "history";
  $("practiceView").hidden = view !== "practice";
  $("overviewBtn").hidden = view === "overview";
  $("historyBtn").hidden = view === "history";
}

function stopPracticePlayback() {
  document.querySelectorAll("#practiceView audio, #practiceView video").forEach((media) => {
    try {
      media.pause();
      if (Number.isFinite(media.currentTime)) media.currentTime = 0;
    } catch {
      // Ignore stale media elements while the practice view is re-rendering.
    }
  });
}

function stopListeningQuestionTimer() {
  if (state.listeningQuestionTimer) window.clearInterval(state.listeningQuestionTimer);
  state.listeningQuestionTimer = null;
}

async function showHistory() {
  stopTimer();
  stopPracticePlayback();
  stopListeningQuestionTimer();
  setView("history");
  window.history.replaceState({}, "", `${window.location.pathname}?view=history`);
  document.title = "CELPIP Practice History";
  if (!SERVER_API_ENABLED) {
    $("historySummary").textContent = "Browser progress only";
    $("historyBody").innerHTML = `<tr><td colspan="7">Server history is available only when running the local app server.</td></tr>`;
    $("historyNotice").textContent = "";
    return;
  }
  $("historyBody").innerHTML = `<tr><td colspan="7">Loading saved attempts...</td></tr>`;
  $("historyNotice").textContent = "";

  try {
    const response = await fetch("/api/submissions");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const attempts = (await response.json()).attempts || [];
    $("historySummary").textContent = `${attempts.length} saved attempt${attempts.length === 1 ? "" : "s"}`;
    if (!attempts.length) {
      $("historyBody").innerHTML = `<tr><td colspan="7">No submitted sections yet.</td></tr>`;
      return;
    }
    $("historyBody").innerHTML = attempts.map((attempt) => {
      const test = TESTS.find((item) => item.id === attempt.test_id);
      const date = new Date(attempt.submitted_at || attempt.created_at).toLocaleString();
      return `<tr>
        <td>${escapeHtml(date)}</td>
        <td>${escapeHtml(test?.label || attempt.test_id)}</td>
        <td>${escapeHtml(attempt.section)}</td>
        <td>${escapeHtml(attempt.raw_score || "Saved")}</td>
        <td>${escapeHtml(attempt.estimated_level || "—")}</td>
        <td>${attempt.elapsed_seconds === null || attempt.elapsed_seconds === undefined ? "—" : formatDuration(attempt.elapsed_seconds)}</td>
        <td><button class="small practice-again" data-test="${attempt.test_id}" data-section="${attempt.section}" type="button">Practice Again</button></td>
      </tr>`;
    }).join("");
    bindPracticeAgainButtons($("historyBody"));
  } catch (error) {
    $("historyBody").innerHTML = `<tr><td colspan="7">Could not load history.</td></tr>`;
    $("historyNotice").textContent = error.message;
  }
}

async function showOverview() {
  stopTimer();
  stopPracticePlayback();
  stopListeningQuestionTimer();
  setView("overview");
  window.history.replaceState({}, "", `${window.location.pathname}?view=overview`);
  document.title = "CELPIP Practice Overview";
  $("overviewBody").innerHTML = `<tr><td colspan="5">Loading practice history...</td></tr>`;
  $("overviewNotice").textContent = "";

  let attempts = [];
  let savedDrafts = [];
  if (SERVER_API_ENABLED) {
    try {
      const [submissionResponse, draftResponse] = await Promise.all([
        fetch("/api/submissions"),
        fetch("/api/drafts"),
      ]);
      if (!submissionResponse.ok || !draftResponse.ok) {
        throw new Error(`HTTP ${submissionResponse.status}/${draftResponse.status}`);
      }
      attempts = (await submissionResponse.json()).attempts || [];
      savedDrafts = (await draftResponse.json()).drafts || [];
    } catch (error) {
      $("overviewNotice").textContent = `SQLite history unavailable; showing browser progress only. ${error.message}`;
    }
  }

  const latest = new Map();
  for (const attempt of attempts) {
    const key = `${attempt.test_id}:${attempt.section}`;
    if (!latest.has(key)) latest.set(key, attempt);
  }
  const databaseDrafts = new Map(savedDrafts.map((draft) => [draft.test_id, draft]));

  let completed = 0;
  let inProgress = 0;
  $("overviewBody").innerHTML = TESTS.map((test) => {
    const localDraft = readLocalDraft(test.id);
    const databaseDraft = databaseDrafts.get(test.id) || { answers: {}, checked: {}, submissions: {} };
    const draft = {
      answers: mergeAnswers(databaseDraft.answers, localDraft.answers),
      checked: { ...databaseDraft.checked, ...localDraft.checked },
      submissions: mergeSubmissions(databaseDraft.submissions, localDraft.submissions),
    };
    const cells = SECTIONS.map((section) => {
      const attempt = latest.get(`${test.id}:${section.id}`);
      const localSubmission = draft.submissions[section.id];
      const answered = Object.entries(draft.answers)
        .filter(([key, value]) => key.includes(`_${section.id}_`) && Boolean(value)).length;
      let status = "not-started";
      let label = "Not started";
      let detail = "Start section";

      if (attempt || localSubmission) {
        completed += 1;
        status = "completed";
        label = "Completed";
        const result = attempt || localSubmission;
        detail = result.raw_score || (result.correct !== null && result.correct !== undefined ? `${result.correct}/${result.total}` : "Submitted");
        const level = displayLevelForResult(section.id, result);
        if (level) detail += ` · Level ${level}`;
      } else if (answered > 0) {
        inProgress += 1;
        status = "in-progress";
        label = "In progress";
        detail = `${answered} answered`;
      }

      return `<td><button class="status-button ${status}" data-test="${test.id}" data-section="${section.id}" type="button">
        <span>${label}</span><small>${escapeHtml(detail)}</small>
      </button></td>`;
    }).join("");
    return `<tr><td class="test-name">${escapeHtml(test.label)}</td>${cells}</tr>`;
  }).join("");

  const total = TESTS.length * SECTIONS.length;
  $("overviewSummary").textContent = `${completed} completed · ${inProgress} in progress · ${total - completed - inProgress} not started`;
  $("overviewBody").querySelectorAll("button[data-test]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.testId = button.dataset.test;
      state.section = button.dataset.section;
      state.index = 0;
      state.sectionIntro = isIntroSection(state.section);
      state.sourceCache.clear();
      await loadTest();
    });
  });
}

function persist({ sync = true } = {}) {
  localStorage.setItem(storageKey(), JSON.stringify(state.answers));
  localStorage.setItem(checkedStorageKey(), JSON.stringify(state.checked));
  localStorage.setItem(submissionStorageKey(), JSON.stringify(state.submissions));
  localStorage.setItem(timingStorageKey(), JSON.stringify(state.timings));
  localStorage.setItem(notesStorageKey(), JSON.stringify(state.notes));
  if (sync) scheduleDraftSync(state.testId);
}

function readLocalDraft(testId) {
  return {
    test_id: testId,
    answers: JSON.parse(localStorage.getItem(storageKey(testId)) || "{}"),
    checked: JSON.parse(localStorage.getItem(checkedStorageKey(testId)) || "{}"),
    submissions: JSON.parse(localStorage.getItem(submissionStorageKey(testId)) || "{}"),
    timings: JSON.parse(localStorage.getItem(timingStorageKey(testId)) || "{}"),
    notes: JSON.parse(localStorage.getItem(notesStorageKey(testId)) || "{}"),
    updated_at: new Date().toISOString(),
  };
}

function draftHasContent(draft) {
  return Object.keys(draft.answers).length
    || Object.keys(draft.checked).length
    || Object.keys(draft.submissions).length
    || Object.keys(draft.timings).length
    || Object.keys(draft.notes).length;
}

function syncAllLocalDrafts() {
  TESTS.forEach((test) => {
    const draft = readLocalDraft(test.id);
    if (draftHasContent(draft)) scheduleDraftSync(test.id, 0);
  });
}

function scheduleDraftSync(testId = state.testId, delay = 600) {
  if (!SERVER_API_ENABLED) return;
  if (state.draftSyncTimers.has(testId)) {
    window.clearTimeout(state.draftSyncTimers.get(testId));
  }
  state.draftSyncTimers.set(
    testId,
    window.setTimeout(() => {
      state.draftSyncTimers.delete(testId);
      syncDraftToDatabase(testId);
    }, delay),
  );
}

async function syncDraftToDatabase(testId = state.testId) {
  if (!SERVER_API_ENABLED) return;
  const draft = readLocalDraft(testId);
  try {
    const response = await fetch("/api/drafts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    state.dbStatus[testId] = { ok: true, updated_at: result.updated_at };
  } catch (error) {
    state.dbStatus[testId] = { ok: false, error: error.message || "Could not sync draft to SQLite." };
  }
}

function renderSections() {
  $("sectionTabs").innerHTML = SECTIONS.map((section) => {
    const total = state.data.sections?.[section.id] || 0;
    return `<button class="section-tab ${section.id === state.section ? "active" : ""}" data-section="${section.id}">
      ${section.label}<small>${total} tasks</small>
    </button>`;
  }).join("");
  $("sectionTabs").querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", async () => {
      stopPracticePlayback();
      stopListeningQuestionTimer();
      state.section = button.dataset.section;
      state.index = 0;
      state.sectionIntro = isIntroSection(state.section);
      stopTimer();
      resetSectionTimer();
      renderSections();
      await render();
    });
  });
}

async function render() {
  stopPracticePlayback();
  const groups = sectionGroups();
  if (!groups.length) return;
  if (state.index >= groups.length) state.index = groups.length - 1;
  const group = currentGroup();
  const submitted = Boolean(state.submissions[state.section]);
  const showSpeakingIntro = state.section === "speaking" && state.sectionIntro;
  const showWritingIntro = state.section === "writing" && state.sectionIntro && !submitted;
  const showSectionIntro = showSpeakingIntro || showWritingIntro;
  updatePracticeUrl(group);
  $("partLabel").textContent = `${TESTS.find((t) => t.id === state.testId).label} · ${state.section}`;
  const displayTitle = displayGroupTitle(group);
  $("questionTitle").textContent = showSectionIntro
    ? `${SECTIONS.find((item) => item.id === state.section)?.label || "Practice"} Test`
    : `${displayPartLabel()}: ${displayTitle} · ${group.questions.length} question${group.questions.length > 1 ? "s" : ""}`;
  const instruction = partInstruction(group);
  $("questionText").textContent = showSectionIntro ? "" : instruction;
  $("questionText").hidden = showSectionIntro || !instruction;
  renderQuestionNav(groups);
  $("questionNav").hidden = showSectionIntro;
  renderStats();
  renderSectionResult();
  updateTimer();
  if (showSpeakingIntro) renderSpeakingIntro(groups);
  else if (showWritingIntro) renderWritingIntro(groups);
  else renderQuestionSet(group, group.media || []);
  const hideSource = (state.section === "listening" && !submitted)
    || state.section === "writing"
    || state.section === "speaking";
  document.querySelector(".source-panel").hidden = hideSource;
  document.querySelector(".practice-grid").classList.toggle("question-only", hideSource);
  $("sourcePanelTitle").textContent = state.section === "listening" ? "Transcript" : "Source";
  if (!hideSource) await renderSource(group, state.section === "listening" && submitted);
  const speakingComplete = state.section === "speaking"
    && sectionQuestions().length > 0
    && sectionQuestions().every((question) => state.answers[question.key]);
  const strictSequence = ["listening", "reading", "writing", "speaking"].includes(state.section) && !submitted;
  $("submitSectionBtn").hidden = submitted
    || showSectionIntro
    || (strictSequence && !(state.section === "speaking" && speakingComplete));
  $("prevBtn").hidden = strictSequence || showSectionIntro;
  $("nextBtn").hidden = showSectionIntro || (["listening", "speaking"].includes(state.section) && !submitted);
  $("prevBtn").disabled = state.index === 0;
  const isLastPart = state.index === groups.length - 1;
  const nextSubmits = isLastPart && !submitted && ["reading", "writing"].includes(state.section);
  const unansweredInPart = group.questions.filter((question) => !state.answers[question.key]).length;
  const readingIncomplete = state.section === "reading" && !submitted && unansweredInPart > 0;
  $("nextBtn").disabled = readingIncomplete || (isLastPart && !nextSubmits);
  $("nextBtn").textContent = nextSubmits ? "Submit Section" : "Next";
  $("nextBtn").title = readingIncomplete
    ? `Answer ${unansweredInPart} remaining question${unansweredInPart === 1 ? "" : "s"} before continuing.`
    : "";
}

function handleNext() {
  const isLastPart = state.index === sectionGroups().length - 1;
  if (isLastPart && !state.submissions[state.section] && ["reading", "writing"].includes(state.section)) {
    submitSection();
    return;
  }
  if (state.section === "reading" && !state.submissions.reading) {
    const unanswered = currentGroup().questions.filter((question) => !state.answers[question.key]).length;
    if (unanswered && !window.confirm(`You have ${unanswered} unanswered question${unanswered === 1 ? "" : "s"} in this Part. CELPIP does not allow returning after you continue. Leave this Part?`)) return;
  }
  moveQuestion(1);
}

function urlTestId(testId) {
  return testId.replace(/^local_/, "").replace("_", "-");
}

function testIdFromUrl(value) {
  return TESTS.find((test) => urlTestId(test.id) === value)?.id || null;
}

function readUrlRoute() {
  const params = new URLSearchParams(window.location.search);
  const testId = testIdFromUrl(params.get("test"));
  const section = params.get("section");
  if (!testId || !SECTIONS.some((item) => item.id === section)) return null;
  const part = Number.parseInt(params.get("part") || "1", 10);
  return {
    testId,
    section,
    partIndex: Number.isFinite(part) && part > 0 ? part - 1 : 0,
    sectionIntro: routeSectionIntro(section, params),
  };
}

function updatePracticeUrl(group) {
  const params = new URLSearchParams({
    test: urlTestId(state.testId),
    section: state.section,
    part: String(state.index + 1),
  });
  if (state.sectionIntro) params.set("intro", "1");
  else if (state.section === "writing" && !state.submissions[state.section]) params.set("intro", "0");
  window.history.replaceState({}, "", `${window.location.pathname}?${params}`);
  const testLabel = TESTS.find((test) => test.id === state.testId)?.label || state.testId;
  document.title = `${testLabel} · ${state.section} · Part ${state.index + 1}`;
}

function isIntroSection(section) {
  return section === "speaking" || section === "writing";
}

function routeSectionIntro(section, params) {
  if (section === "speaking") return params.get("intro") === "1";
  if (section === "writing") return params.get("intro") !== "0";
  return false;
}

function partInstruction(group) {
  if (state.section === "listening") return "Listen to the passage, then answer the questions in this part.";
  if (state.section === "reading") return "Read the source material, then answer the questions in this part.";
  return "";
}

function renderQuestionNav(groups) {
  $("questionNav").innerHTML = groups.map((group, i) => {
    const answered = group.questions.filter((q) => state.answers[q.key]).length;
    const checked = group.questions.filter((q) => state.checked[q.key] !== undefined && state.checked[q.key] !== null).length;
    const wrong = group.questions.some((q) => state.checked[q.key] === false);
    const allCorrect = checked > 0 && group.questions.every((q) => q.question_type !== "multiple_choice_single" || state.checked[q.key] === true);
    const status = allCorrect ? "correct" : wrong ? "wrong" : answered ? "answered" : "";
    const locked = ["listening", "reading", "writing", "speaking"].includes(state.section)
      && !state.submissions[state.section]
      && i !== state.index;
    return `<button class="q-dot part-dot ${i === state.index ? "active" : ""} ${status}" data-index="${i}" title="${escapeHtml(displayGroupTitle(group, i))}" ${locked ? "disabled" : ""}>
      <span>${groupNavLabel(groups, group, i)}</span><small>${answered}/${group.questions.length}</small>
    </button>`;
  }).join("");
  $("questionNav").querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", async () => {
      state.index = Number(button.dataset.index);
      await render();
    });
  });
}

function groupNavLabel(groups, group, index) {
  if (state.section === "listening") return listeningPartLabel(index);
  if (state.section === "speaking") {
    const number = group.questions[0]?.number;
    if (!number) return "P";
    const duplicates = groups.filter((item) => item.questions[0]?.number === number);
    if (duplicates.length > 1) return `${number}${duplicates.indexOf(group) === 0 ? "A" : "B"}`;
    return String(number);
  }
  return String(index + 1);
}

function displayPartLabel(index = state.index) {
  if (state.section === "listening") return `Part ${listeningPartLabel(index)}`;
  return `Part ${index + 1}`;
}

function listeningPartLabel(index = state.index) {
  return LISTENING_PART_LABELS[index] || String(index + 1);
}

function listeningGroupTimerSeconds(index = state.index) {
  return LISTENING_GROUP_TIMERS[listeningPartLabel(index)] || null;
}

function renderStats() {
  const questions = sectionQuestions();
  const answered = questions.filter((q) => state.answers[q.key]).length;
  const correct = questions.filter((q) => state.checked[q.key] === true).length;
  $("answeredCount").textContent = `${answered}/${questions.length}`;
  $("scoreCount").textContent = `${correct}`;
}

function sectionChoiceQuestions() {
  return sectionQuestions().filter((q) => q.question_type === "multiple_choice_single");
}

function renderSectionResult() {
  const box = $("sectionResult");
  const result = state.submissions[state.section];
  if (!result) {
    box.hidden = true;
    return;
  }

  box.hidden = false;
  if (state.section === "writing") {
    const assessment = result.writing_assessment;
    const dbLine = resultStorageNotice(result);
    if (assessment) {
      box.innerHTML = `<strong>AI Practice Level ${escapeHtml(assessment.overall_level)}</strong>
        ${escapeHtml(assessment.summary)}
        <small>${escapeHtml(assessment.disclaimer || result.note)}</small>
        ${dbLine}
        <button class="practice-again" data-test="${state.testId}" data-section="${state.section}" type="button">Practice Again</button>`;
    } else {
      box.innerHTML = `<strong>Writing Saved</strong>
        ${escapeHtml(result.ai_error || result.note)}
        ${dbLine}
        ${result.db_attempt_id ? `<button class="retry-writing-assessment" type="button">Grade with AI</button>` : ""}
        <button class="practice-again" data-test="${state.testId}" data-section="${state.section}" type="button">Practice Again</button>`;
    }
  } else if (state.section === "speaking") {
    const assessment = result.speaking_assessment;
    const dbLine = resultStorageNotice(result);
    if (assessment) {
      box.innerHTML = `<strong>AI Practice Level ${escapeHtml(assessment.overall_level)}</strong>
        ${escapeHtml(assessment.summary)}
        <small>${escapeHtml(assessment.disclaimer || result.note)}</small>
        ${dbLine}
        <button class="practice-again" data-test="${state.testId}" data-section="${state.section}" type="button">Practice Again</button>`;
    } else {
      box.innerHTML = `<strong>Speaking Saved</strong>
        ${escapeHtml(result.ai_error || result.note)}
        ${dbLine}
        ${result.db_attempt_id ? `<button class="retry-speaking-assessment" type="button">Grade with AI</button>` : ""}
        <button class="practice-again" data-test="${state.testId}" data-section="${state.section}" type="button">Practice Again</button>`;
    }
  } else if (result.level || Number.isFinite(result.correct)) {
    const displayLevel = displayLevelForResult(state.section, result);
    const dbLine = resultStorageNotice(result);
    box.innerHTML = `<strong>${displayLevel ? `Level ${escapeHtml(displayLevel)}` : "Practice Score"}</strong>
      Raw score: ${result.correct}/${result.total}
      <small>${escapeHtml(result.note)}</small>
      ${dbLine}
      <button class="practice-again" data-test="${state.testId}" data-section="${state.section}" type="button">Practice Again</button>`;
  } else {
    const dbLine = resultStorageNotice(result);
    box.innerHTML = `<strong>Saved</strong>
      ${escapeHtml(result.note)}
      ${dbLine}
      <button class="practice-again" data-test="${state.testId}" data-section="${state.section}" type="button">Practice Again</button>`;
  }
  bindPracticeAgainButtons(box);
  const writingRetryButton = box.querySelector(".retry-writing-assessment");
  if (writingRetryButton) writingRetryButton.addEventListener("click", retryWritingAssessment);
  const speakingRetryButton = box.querySelector(".retry-speaking-assessment");
  if (speakingRetryButton) speakingRetryButton.addEventListener("click", retrySpeakingAssessment);
}

function resultStorageNotice(result) {
  return result.db_error ? `<small>Local only: ${escapeHtml(result.db_error)}</small>` : "";
}

async function retryWritingAssessment() {
  const submission = state.submissions.writing;
  if (!submission?.db_attempt_id) return;
  const button = document.querySelector(".retry-writing-assessment");
  if (button) {
    button.disabled = true;
    button.textContent = "Grading...";
  }
  try {
    const response = await fetch("/api/writing-assessments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ attempt_id: submission.db_attempt_id }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    submission.writing_assessment = result.writing_assessment;
    submission.level = String(result.writing_assessment.overall_level);
    submission.note = result.writing_assessment.disclaimer;
    delete submission.ai_error;
    persist();
    await render();
  } catch (error) {
    submission.ai_error = error.message || "AI assessment failed.";
    persist();
    renderSectionResult();
  }
}

async function retrySpeakingAssessment() {
  const submission = state.submissions.speaking;
  if (!submission?.db_attempt_id) return;
  const button = document.querySelector(".retry-speaking-assessment");
  if (button) {
    button.disabled = true;
    button.textContent = "Grading...";
  }
  try {
    const response = await fetch("/api/speaking-assessments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ attempt_id: submission.db_attempt_id }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    submission.speaking_assessment = result.speaking_assessment;
    submission.level = String(result.speaking_assessment.overall_level);
    submission.note = result.speaking_assessment.disclaimer;
    delete submission.ai_error;
    persist();
    await render();
  } catch (error) {
    submission.ai_error = error.message || "AI assessment failed.";
    persist();
    renderSectionResult();
  }
}

function bindPracticeAgainButtons(container) {
  container.querySelectorAll(".practice-again").forEach((button) => {
    button.addEventListener("click", () => startFreshSection(button.dataset.test, button.dataset.section));
  });
}

async function startFreshSection(testId, section) {
  const test = TESTS.find((item) => item.id === testId);
  const sectionLabel = SECTIONS.find((item) => item.id === section)?.label || section;
  if (!window.confirm(`Start a new ${sectionLabel} practice for ${test?.label || testId}? Previous submitted attempts will remain in History.`)) return;

  const response = await fetch(materialUrl(testId, "questions.json"));
  const data = await response.json();
  const keys = new Set(data.questions.filter((question) => question.section === section).map((question) => question.key));
  const localDraft = readLocalDraft(testId);
  const databaseDraft = await fetchDatabaseDraft(testId);
  const draft = {
    test_id: testId,
    answers: mergeAnswers(databaseDraft?.answers, localDraft.answers),
    checked: { ...(databaseDraft?.checked || {}), ...localDraft.checked },
    submissions: mergeSubmissions(databaseDraft?.submissions, localDraft.submissions),
    timings: { ...(databaseDraft?.timings || {}), ...localDraft.timings },
    notes: { ...(databaseDraft?.notes || {}), ...localDraft.notes },
    updated_at: new Date().toISOString(),
  };
  keys.forEach((key) => {
    delete draft.answers[key];
    delete draft.checked[key];
  });
  // Keep an explicit reset marker so the server does not immediately restore
  // this section from the latest completed attempt when the draft is reloaded.
  draft.submissions[section] = null;
  delete draft.timings[section];

  localStorage.setItem(storageKey(testId), JSON.stringify(draft.answers));
  localStorage.setItem(checkedStorageKey(testId), JSON.stringify(draft.checked));
  localStorage.setItem(submissionStorageKey(testId), JSON.stringify(draft.submissions));
  localStorage.setItem(timingStorageKey(testId), JSON.stringify(draft.timings));
  localStorage.setItem(notesStorageKey(testId), JSON.stringify(draft.notes));
  if (SERVER_API_ENABLED) {
    await fetch("/api/drafts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    });
  }

  state.testId = testId;
  state.section = section;
  state.index = 0;
  state.sectionIntro = isIntroSection(section);
  for (const key of [...state.listeningUnlocked]) {
    if (key.startsWith(`${testId}:`)) state.listeningUnlocked.delete(key);
  }
  state.sourceCache.clear();
  await loadTest();
}

function mediaNode(media) {
  const src = assetUrl(media.path);
  if (media.type === "audio") return `<audio controls preload="metadata" src="${src}"></audio>`;
  if (media.type === "video" || /\.(mp4|webm)$/i.test(media.path)) return `<video controls src="${src}"></video>`;
  if (media.type === "image") return `<img src="${src}" alt="">`;
  return `<a href="${src}" target="_blank" rel="noreferrer">${media.path}</a>`;
}

function renderSpeakingIntro(groups) {
  const submitted = Boolean(state.submissions.speaking);
  const answered = sectionQuestions().filter((question) => state.answers[question.key]).length;
  const hasProgress = answered > 0 && !submitted;
  const primaryLabel = submitted ? "Review Speaking" : hasProgress ? "Continue Speaking" : "Begin Speaking";
  $("mediaArea").innerHTML = "";
  $("answerArea").innerHTML = `<section class="section-start-panel">
    <div>
      <p class="eyebrow">Speaking</p>
      <h2>${escapeHtml(TESTS.find((test) => test.id === state.testId)?.label || state.testId)}</h2>
      <p>${groups.length} tasks · ${submitted ? "Completed" : hasProgress ? `${answered} response${answered === 1 ? "" : "s"} saved` : "Ready to start"}</p>
    </div>
    <div class="section-start-actions">
      <button class="start-speaking-section" type="button">${primaryLabel}</button>
      ${(submitted || hasProgress) ? `<button class="practice-again ghost" data-test="${state.testId}" data-section="speaking" type="button">Practice Again</button>` : ""}
    </div>
  </section>`;
  $("feedback").hidden = true;
  const startButton = $("answerArea").querySelector(".start-speaking-section");
  startButton.addEventListener("click", async () => {
    state.sectionIntro = false;
    state.index = 0;
    await render();
  });
  bindPracticeAgainButtons($("answerArea"));
}

function renderWritingIntro(groups) {
  const answered = sectionQuestions().filter((question) => state.answers[question.key]).length;
  const elapsed = Number(state.timings.writing?.elapsed_seconds) || 0;
  const hasProgress = answered > 0 || elapsed > 0;
  const primaryLabel = hasProgress ? "Continue Writing" : "Begin Writing";
  $("mediaArea").innerHTML = "";
  $("answerArea").innerHTML = `<section class="section-start-panel">
    <div>
      <p class="eyebrow">Writing</p>
      <h2>${escapeHtml(TESTS.find((test) => test.id === state.testId)?.label || state.testId)}</h2>
      <p>${groups.length} tasks · ${hasProgress ? "Draft in progress" : "Timer starts when you begin"}</p>
    </div>
    <div class="section-start-actions">
      <button class="start-writing-section" type="button">${primaryLabel}</button>
      ${hasProgress ? `<button class="practice-again ghost" data-test="${state.testId}" data-section="writing" type="button">Practice Again</button>` : ""}
    </div>
  </section>`;
  $("feedback").hidden = true;
  const startButton = $("answerArea").querySelector(".start-writing-section");
  startButton.addEventListener("click", async () => {
    state.sectionIntro = false;
    state.index = 0;
    await render();
    if (!state.submissions.writing && !state.timer.running) toggleTimer();
  });
  bindPracticeAgainButtons($("answerArea"));
}

function renderQuestionSet(group, partMedia = []) {
  const listeningLocked = state.section === "listening"
    && !state.submissions[state.section]
    && !state.listeningUnlocked.has(listeningUnlockKey(group));

  if (listeningLocked && partMedia.length) {
    renderListeningGate(group, partMedia[0]);
    return;
  }

  const strictListening = state.section === "listening" && !state.submissions[state.section];
  if (strictListening) {
    const groupSeconds = listeningGroupTimerSeconds(state.index);
    if (groupSeconds) {
      renderListeningQuestionGroup(group, groupSeconds);
      return;
    }
    renderListeningQuestion(group);
    return;
  }

  $("mediaArea").innerHTML = partMedia.length
    ? `<div class="part-media"><strong>Prompt media</strong>${partMedia.map(mediaNode).join("")}</div>`
    : "";
  $("answerArea").innerHTML = group.questions.map((question) => renderQuestionCard(question)).join("");
  group.questions.forEach(bindQuestionCard);
  $("feedback").hidden = true;
}

function renderListeningQuestion(group) {
  if (state.listeningQuestionTimer) window.clearInterval(state.listeningQuestionTimer);
  const key = listeningUnlockKey(group);
  const index = Math.min(state.listeningQuestionIndex.get(key) || 0, group.questions.length - 1);
  state.listeningQuestionIndex.set(key, index);
  const question = group.questions[index];
  $("mediaArea").innerHTML = `<div class="part-complete"><strong>Passage completed</strong><span>Question ${index + 1} of ${group.questions.length}</span></div>`;
  $("answerArea").innerHTML = `${renderQuestionCard(question, true)}
    <div class="listening-question-controls">
      <span>Time remaining: <strong id="listeningQuestionTime">00:30</strong></span>
      <button id="nextListeningQuestion" type="button">${index === group.questions.length - 1 ? "Finish Part" : "Next Question"}</button>
    </div>`;
  bindQuestionCard(question);
  $("feedback").hidden = true;
  $("nextListeningQuestion").addEventListener("click", () => advanceListeningQuestion(group));
  startListeningQuestionTimer(group, 30);
  startListeningQuestionAudio();
}

function renderListeningQuestionGroup(group, seconds) {
  if (state.listeningQuestionTimer) window.clearInterval(state.listeningQuestionTimer);
  $("mediaArea").innerHTML = `<div class="part-complete"><strong>Passage completed</strong><span>${group.questions.length} questions</span></div>`;
  $("answerArea").innerHTML = `${group.questions.map((question) => renderQuestionCard(question)).join("")}
    <div class="listening-question-controls">
      <span>Time remaining: <strong id="listeningQuestionTime">${formatDuration(seconds)}</strong></span>
      <button id="nextListeningQuestion" type="button">Finish Part</button>
    </div>`;
  group.questions.forEach(bindQuestionCard);
  $("feedback").hidden = true;
  $("nextListeningQuestion").addEventListener("click", advanceListeningPart);
  startListeningQuestionTimer(group, seconds, advanceListeningPart);
}

function startListeningQuestionTimer(group, seconds, onExpire = () => advanceListeningQuestion(group)) {
  let remaining = seconds;
  const output = $("listeningQuestionTime");
  state.listeningQuestionTimer = window.setInterval(() => {
    remaining -= 1;
    if (output) output.textContent = formatDuration(remaining);
    if (remaining <= 0) onExpire();
  }, 1000);
}

async function startListeningQuestionAudio() {
  const player = $("listeningQuestionAudio");
  const fallback = $("startQuestionAudio");
  if (!player) return;
  fallback?.addEventListener("click", async () => {
    fallback.disabled = true;
    try {
      await player.play();
      fallback.hidden = true;
    } catch (error) {
      fallback.disabled = false;
      fallback.textContent = `Audio unavailable: ${error.message}`;
    }
  });
  try {
    await player.play();
    if (fallback) fallback.hidden = true;
  } catch {
    if (fallback) fallback.hidden = false;
  }
}

function advanceListeningQuestion(group) {
  if (state.listeningQuestionTimer) window.clearInterval(state.listeningQuestionTimer);
  state.listeningQuestionTimer = null;
  const key = listeningUnlockKey(group);
  const nextIndex = (state.listeningQuestionIndex.get(key) || 0) + 1;
  if (nextIndex < group.questions.length) {
    state.listeningQuestionIndex.set(key, nextIndex);
  } else {
    advanceListeningPart();
    return;
  }
  render();
}

function advanceListeningPart() {
  if (state.listeningQuestionTimer) window.clearInterval(state.listeningQuestionTimer);
  state.listeningQuestionTimer = null;
  if (state.index < sectionGroups().length - 1) {
    state.index += 1;
  } else {
    submitSection();
    return;
  }
  render();
}

function listeningUnlockKey(group) {
  return `${state.testId}:${group.id || group.source_file}`;
}

function renderListeningGate(group, media) {
  const src = assetUrl(media.path);
  const isVideo = media.type === "video" || /\.(mp4|webm)$/i.test(media.path);
  $("mediaArea").innerHTML = `<div class="listening-gate">
    <p class="eyebrow">Listening passage</p>
    <h2>Questions will appear after the ${isVideo ? "video" : "audio"} finishes.</h2>
    <p>This passage plays once and cannot be paused or replayed during the attempt.</p>
    ${isVideo
      ? `<video id="partPassageMedia" src="${src}" preload="metadata" playsinline></video>`
      : `<audio id="partPassageMedia" src="${src}" preload="metadata"></audio>`}
    <div class="passage-progress"><span id="passageProgressBar"></span></div>
    <button id="startPassageBtn" type="button">Start ${isVideo ? "Video" : "Audio"}</button>
    <small id="passageStatus">Use headphones and prepare your notes before starting.</small>
  </div>`;
  $("answerArea").innerHTML = "";
  $("feedback").hidden = true;

  const player = $("partPassageMedia");
  const startButton = $("startPassageBtn");
  const status = $("passageStatus");
  const progress = $("passageProgressBar");
  startButton.addEventListener("click", async () => {
    if (!state.timer.running) toggleTimer();
    startButton.disabled = true;
    startButton.textContent = "Playing...";
    status.textContent = "Listen carefully. Questions remain hidden until playback ends.";
    try {
      await player.play();
    } catch (error) {
      startButton.disabled = false;
      startButton.textContent = `Start ${isVideo ? "Video" : "Audio"}`;
      status.textContent = `Playback could not start: ${error.message}`;
    }
  });
  player.addEventListener("timeupdate", () => {
    if (Number.isFinite(player.duration) && player.duration > 0) {
      progress.style.width = `${Math.min(100, (player.currentTime / player.duration) * 100)}%`;
    }
  });
  player.addEventListener("ended", () => {
    const key = listeningUnlockKey(group);
    state.listeningUnlocked.add(key);
    state.listeningQuestionIndex.set(key, 0);
    render();
  });
}

function renderQuestionCard(q, strictListening = false) {
  const saved = state.answers[q.key];
  const media = q.media?.length
    ? `<div class="card-media">${strictListening ? listeningQuestionMedia(q.media) : q.media.map(mediaNode).join("")}</div>`
    : "";
  const taskMedia = q.question_html ? "" : media;
  if (q.question_type === "multiple_choice_single") {
    const submitted = Boolean(state.submissions[state.section]);
    const options = q.options.map((option) => {
      const selected = saved === option.id || saved === option.value;
      const checked = state.checked[q.key];
      const graded = submitted && checked !== undefined;
      const correctness = graded && option.is_correct ? "correct" : graded && selected && !option.is_correct ? "wrong" : "";
      const text = option.text ? `<span>${escapeHtml(option.text)}</span>` : "";
      const media = option.media?.length ? `<div class="option-media">${option.media.map(mediaNode).join("")}</div>` : "";
      return `<label class="option ${selected ? "selected" : ""} ${correctness}">
        <input type="radio" name="${q.key}" value="${option.id}" ${selected ? "checked" : ""} ${submitted ? "disabled" : ""}>
        <span>${text}${media}</span>
      </label>`;
    }).join("");
    return `<section class="question-card ${submitted ? "review-card" : ""}" data-key="${q.key}">
      <h2>Question ${questionNumber(q)}</h2>
      ${media}
      <div class="card-question-text">${escapeHtml(q.question_text || "")}</div>
      <div class="card-options">${options}</div>
      ${submitted ? questionFeedback(q) : ""}
      ${submitted ? reviewNoteHtml(q) : ""}
    </section>`;
  }

  if (q.question_type === "writing_task") {
    const target = q.timing?.word_count_target;
    const submitted = Boolean(state.submissions.writing);
    return `<section class="question-card" data-key="${q.key}">
      <h2>Task ${questionNumber(q)}</h2>
      ${taskMedia}
      <div class="card-question-text structured-prompt">${structuredQuestionHtml(q)}</div>
      <textarea class="long-response" placeholder="Type your response here..." ${submitted ? "readonly" : ""}>${escapeHtml(saved || "")}</textarea>
      <div class="writing-meta">
        <span class="word-count">0 words</span>
        ${target ? `<span>Target: ${target.min}-${target.max} words</span>` : ""}
        ${q.timing?.time_limit_minutes ? `<span>Suggested time: ${q.timing.time_limit_minutes} minutes</span>` : ""}
      </div>
      ${submitted ? writingAssessmentHtml(q) : ""}
      ${submitted ? responseSamplesHtml(q) : ""}
      ${submitted ? reviewNoteHtml(q) : ""}
    </section>`;
  }

  return `<section class="question-card" data-key="${q.key}">
    <h2>${q.number ? `Task ${q.number}` : "Speaking Practice"}</h2>
    ${taskMedia}
    <div class="card-question-text structured-prompt">${structuredQuestionHtml(q)}</div>
    <div class="speaking-recorder">
      <div class="recorder-actions">
        <button class="record-response" type="button" hidden>Enable Microphone</button>
        <strong class="recording-time">00:00</strong>
      </div>
      <audio class="recorded-playback" preload="metadata" hidden></audio>
      <div class="recorded-player" hidden>
        <button class="recorded-play-toggle" type="button" aria-label="Play recording">▶</button>
        <div class="recorded-progress-track" role="slider" aria-label="Recording progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" tabindex="0">
          <span class="recorded-progress-fill"></span>
        </div>
        <span class="recorded-player-time">00:00 / 00:00</span>
      </div>
      <small class="recorder-status">Task starts automatically.</small>
    </div>
    ${state.submissions.speaking ? speakingAssessmentHtml(q) : ""}
    ${state.submissions.speaking ? reviewNoteHtml(q) : ""}
  </section>`;
}

function reviewNoteHtml(question) {
  const note = state.notes[question.key] || "";
  return `<div class="review-note">
    <label>Review note
      <textarea class="review-note-input" placeholder="Why did I miss this question? What should I notice next time?">${escapeHtml(note)}</textarea>
    </label>
    <small class="review-note-status">Saved automatically</small>
  </div>`;
}

function writingAssessmentHtml(question) {
  const assessment = state.submissions.writing?.writing_assessment;
  const task = assessment?.task_assessments?.find((item) => item.question_key === question.key);
  if (!task) return "";
  const criteria = task.criteria.map((criterion) => `<div class="writing-criterion">
    <div><strong>${escapeHtml(criterion.name)}</strong><span>Level ${escapeHtml(criterion.level)}</span></div>
    <p>${escapeHtml(criterion.feedback)}</p>
  </div>`).join("");
  const strengths = task.strengths.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const improvements = task.improvements.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  return `<section class="writing-assessment" aria-label="AI writing assessment">
    <div class="writing-assessment-heading">
      <h2>AI Assessment</h2>
      <strong>Estimated Level ${escapeHtml(task.estimated_level)}</strong>
    </div>
    <div class="writing-criteria">${criteria}</div>
    <div class="writing-feedback-columns">
      <div><h3>Strengths</h3><ul>${strengths}</ul></div>
      <div><h3>Improve Next</h3><ul>${improvements}</ul></div>
    </div>
  </section>`;
}

function speakingAssessmentHtml(question) {
  const assessment = state.submissions.speaking?.speaking_assessment;
  const task = assessment?.task_assessments?.find((item) => item.question_key === question.key);
  if (!task) return "";
  const criteria = task.criteria.map((criterion) => `<div class="writing-criterion">
    <div><strong>${escapeHtml(criterion.name)}</strong><span>Level ${escapeHtml(criterion.level)}</span></div>
    <p>${escapeHtml(criterion.feedback)}</p>
  </div>`).join("");
  const strengths = task.strengths.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const improvements = task.improvements.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  return `<section class="writing-assessment" aria-label="AI speaking assessment">
    <div class="writing-assessment-heading">
      <h2>AI Assessment</h2>
      <strong>Estimated Level ${escapeHtml(task.estimated_level)}</strong>
    </div>
    <p class="speaking-transcript"><strong>Transcript:</strong> ${escapeHtml(task.transcript)}</p>
    <div class="writing-criteria">${criteria}</div>
    <div class="writing-feedback-columns">
      <div><h3>Strengths</h3><ul>${strengths}</ul></div>
      <div><h3>Improve Next</h3><ul>${improvements}</ul></div>
    </div>
  </section>`;
}

function listeningQuestionMedia(media) {
  let audioAdded = false;
  const nodes = media.map((item) => {
    if (item.type === "audio" && !audioAdded) {
      audioAdded = true;
      return `<audio id="listeningQuestionAudio" src="${assetUrl(item.path)}" preload="auto"></audio>
        <button id="startQuestionAudio" class="small" type="button" hidden>Play question audio</button>`;
    }
    return mediaNode(item);
  });
  return nodes.join("");
}

function sanitizeStructuredHtml(html, sourceFile, removeRecordingNote = false) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  doc.body.querySelectorAll("script, style, iframe, form").forEach((node) => node.remove());
  if (removeRecordingNote) {
    doc.body.querySelectorAll("p").forEach((node) => {
      if (/not recording your response/i.test(node.textContent || "")) node.remove();
    });
  }
  doc.body.querySelectorAll("*").forEach((node) => {
    for (const attribute of [...node.attributes]) {
      if (attribute.name.toLowerCase().startsWith("on")) node.removeAttribute(attribute.name);
    }
    node.style?.removeProperty("min-height");
    if (Number.parseInt(node.style?.marginTop || "0", 10) > 40) node.style.removeProperty("margin-top");
  });
  doc.body.querySelectorAll("[src]").forEach((node) => {
    const src = node.getAttribute("src");
    if (!src || /^(https?:)?\/\//.test(src) || src.startsWith("data:")) return;
    node.setAttribute("src", assetUrl(resolveRelativePath(sourceFile, src)));
  });
  return doc.body.innerHTML;
}

function structuredQuestionHtml(question) {
  if (!question.question_html) return escapeHtml(question.question_text || "");
  const sourceFile = question.source_file || question.source_pages?.[0]?.file;
  return sanitizeStructuredHtml(question.question_html, sourceFile, true);
}

function responseSamplesHtml(question) {
  if (!question.response_samples?.length) return "";
  const sourceFile = question.source_file || question.source_pages?.[0]?.file;
  const tabs = question.response_samples.map((sample, index) => `
    <button class="response-sample-tab ${index === 0 ? "active" : ""}" type="button" data-sample-index="${index}" aria-selected="${index === 0}">
      ${escapeHtml(sample.level)}
    </button>`).join("");
  const panels = question.response_samples.map((sample, index) => `
    <div class="response-sample-panel structured-prompt" data-sample-panel="${index}" ${index === 0 ? "" : "hidden"}>
      <h3>${escapeHtml(sample.title)}</h3>
      ${sanitizeStructuredHtml(sample.html || escapeHtml(sample.text || ""), sourceFile)}
    </div>`).join("");
  return `<section class="response-samples" aria-label="Sample responses">
    <div class="response-samples-heading">
      <h2>Sample Responses</h2>
      <span>Compare after submission</span>
    </div>
    <div class="response-sample-tabs" role="tablist" aria-label="CELPIP level">${tabs}</div>
    ${panels}
  </section>`;
}

function bindQuestionCard(q) {
  const card = document.querySelector(`[data-key="${CSS.escape(q.key)}"]`);
  if (!card) return;
  card.querySelectorAll("input[type='radio']").forEach((input) => {
    input.addEventListener("change", () => {
      state.answers[q.key] = input.value;
      if (state.submissions[q.section]) {
        delete state.submissions[q.section];
        for (const question of sectionQuestions()) {
          delete state.checked[question.key];
        }
      }
      persist();
      if (q.section === "listening" && !state.submissions[q.section]) {
        card.querySelectorAll(".option").forEach((option) => option.classList.remove("selected"));
        input.closest(".option")?.classList.add("selected");
        renderStats();
      } else {
        render();
      }
    });
  });
  const textarea = card.querySelector("textarea.long-response");
  if (textarea) {
    const update = () => {
      state.answers[q.key] = textarea.value;
      const count = card.querySelector(".word-count");
      if (count) count.textContent = `${countWords(textarea.value)} words`;
      persist();
      renderStats();
    };
    textarea.addEventListener("input", update);
    update();
  }
  const reviewNote = card.querySelector(".review-note-input");
  if (reviewNote) {
    let savedTimer;
    const status = card.querySelector(".review-note-status");
    reviewNote.addEventListener("input", () => {
      state.notes[q.key] = reviewNote.value;
      if (status) status.textContent = "Saving...";
      persist();
      window.clearTimeout(savedTimer);
      savedTimer = window.setTimeout(() => {
        if (status) status.textContent = "Saved automatically";
      }, 700);
    });
  }
  card.querySelectorAll(".response-sample-tab").forEach((button) => {
    button.addEventListener("click", () => {
      const selectedIndex = button.dataset.sampleIndex;
      card.querySelectorAll(".response-sample-tab").forEach((tab) => {
        const selected = tab === button;
        tab.classList.toggle("active", selected);
        tab.setAttribute("aria-selected", String(selected));
      });
      card.querySelectorAll("[data-sample-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.samplePanel !== selectedIndex;
      });
    });
  });
  if (q.section === "speaking") bindSpeakingRecorder(card, q);
}

async function bindSpeakingRecorder(card, question) {
  const recordButton = card.querySelector(".record-response");
  const playback = card.querySelector(".recorded-playback");
  const playbackUi = bindRecordedPlayback(card, playback);
  const status = card.querySelector(".recorder-status");
  const time = card.querySelector(".recording-time");
  if (state.submissions.speaking) {
    recordButton.hidden = true;
    status.textContent = "Loading recorded response...";
  }
  const prep = question.timing?.preparation_seconds || 0;
  const limit = question.timing?.recording_seconds || 0;
  if (!recordButton || (limit && (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder))) {
    if (recordButton) recordButton.disabled = true;
    status.textContent = "Audio recording is not supported by this browser.";
    return;
  }

  let recorder;
  let stream;
  let chunks = [];
  let startedAt = 0;
  let timerId;
  let started = false;
  let recordingStarted = false;
  let preparationComplete = false;
  let micSetupPromise = null;

  const showMicFallback = (message, label = "Enable Microphone") => {
    recordButton.textContent = label;
    recordButton.hidden = false;
    recordButton.disabled = false;
    status.textContent = message;
  };

  const setupRecorder = async () => {
    if (!limit || recorder) return recorder;
    if (!micSetupPromise) {
      micSetupPromise = (async () => {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const preferredType = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"]
          .find((type) => MediaRecorder.isTypeSupported(type));
        recorder = new MediaRecorder(stream, preferredType ? { mimeType: preferredType } : undefined);
        recorder.addEventListener("dataavailable", (event) => {
          if (event.data.size) chunks.push(event.data);
        });
        recorder.addEventListener("stop", async () => {
          window.clearInterval(timerId);
          stream?.getTracks().forEach((track) => track.stop());
          const duration = Math.max(1, (Date.now() - startedAt) / 1000);
          const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
          status.textContent = "Saving recording...";
          await uploadSpeakingRecording(question, blob, duration, status, playback);
        });
        return recorder;
      })().catch((error) => {
        micSetupPromise = null;
        throw error;
      });
    }
    return micSetupPromise;
  };

  const beginRecording = async () => {
    window.clearInterval(timerId);
    preparationComplete = true;
    if (!limit) {
      stream?.getTracks().forEach((track) => track.stop());
      state.answers[question.key] = "preparation-complete";
      persist();
      advanceSpeakingTask();
      return;
    }
    if (recordingStarted) return;
    try {
      recordButton.disabled = true;
      status.textContent = "Starting recording";
      await setupRecorder();
      recordingStarted = true;
      recordButton.hidden = true;
      recorder.start(250);
      startedAt = Date.now();
      let recordingRemaining = limit || 60;
      status.textContent = "Recording in progress";
      time.textContent = formatDuration(recordingRemaining);
      timerId = window.setInterval(() => {
        recordingRemaining -= 1;
        time.textContent = formatDuration(recordingRemaining);
        if (recordingRemaining <= 0 && recorder.state === "recording") recorder.stop();
      }, 1000);
    } catch (error) {
      stream?.getTracks().forEach((track) => track.stop());
      showMicFallback(`Microphone unavailable: ${error.message}. Click Start Recording to retry.`, "Start Recording");
    }
  };

  const startSpeakingTask = () => {
    if (started || state.answers[question.key] || state.submissions.speaking) return;
    started = true;
    try {
      chunks = [];
      recordButton.disabled = true;
      recordButton.hidden = true;
      let preparationRemaining = prep;
      status.textContent = "Preparation time";
      time.textContent = formatDuration(preparationRemaining);
      if (limit) {
        setupRecorder().catch((error) => {
          if (!preparationComplete) {
            showMicFallback(`Click Enable Microphone so recording can start automatically. ${error.message}`);
          }
        });
      }

      if (preparationRemaining <= 0) {
        beginRecording();
      } else {
        timerId = window.setInterval(() => {
          preparationRemaining -= 1;
          time.textContent = formatDuration(preparationRemaining);
          if (preparationRemaining <= 0) beginRecording();
        }, 1000);
      }
    } catch (error) {
      stream?.getTracks().forEach((track) => track.stop());
      started = false;
      showMicFallback(`Unable to start task: ${error.message}. Click Enable Microphone to retry.`);
    }
  };

  recordButton.addEventListener("click", async () => {
    recordButton.disabled = true;
    try {
      await setupRecorder();
      recordButton.hidden = true;
      if (preparationComplete) beginRecording();
      else status.textContent = "Preparation time";
    } catch (error) {
      const label = preparationComplete ? "Start Recording" : "Enable Microphone";
      showMicFallback(`Microphone unavailable: ${error.message}. Click ${label} to retry.`, label);
    }
  });

  try {
    const params = new URLSearchParams({ test_id: state.testId, question_key: question.key });
    const response = await fetch(`/api/recordings?${params}`);
    const recordings = response.ok ? (await response.json()).recordings || [] : [];
    if (recordings.length) {
      if (state.submissions.speaking) {
        playbackUi.show(recordings[0]);
        recordButton.hidden = true;
        status.textContent = `Recorded response saved ${new Date(recordings[0].created_at).toLocaleString()}.`;
        time.textContent = formatDuration(recordings[0].duration_seconds || 0);
      } else if (state.answers[question.key]) {
        recordButton.disabled = true;
        status.textContent = "Response recorded. Continue to the next task.";
        time.textContent = formatDuration(recordings[0].duration_seconds || 0);
      }
    }
  } catch {
    // Recording remains available for the current page even if history lookup fails.
  }
  window.setTimeout(startSpeakingTask, 300);
}

function bindRecordedPlayback(card, playback) {
  const player = card.querySelector(".recorded-player");
  const toggle = card.querySelector(".recorded-play-toggle");
  const track = card.querySelector(".recorded-progress-track");
  const fill = card.querySelector(".recorded-progress-fill");
  const label = card.querySelector(".recorded-player-time");
  if (!playback || !player || !toggle || !track || !fill || !label) {
    return {
      show(recording) {
        if (!playback || !recording?.url) return;
        playback.src = recording.url;
        playback.hidden = false;
      },
    };
  }

  const update = () => {
    const duration = Number.isFinite(playback.duration) && playback.duration > 0
      ? playback.duration
      : Number(player.dataset.durationSeconds) || 0;
    const current = Number.isFinite(playback.currentTime) ? playback.currentTime : 0;
    const percent = duration > 0 ? Math.min(100, Math.max(0, (current / duration) * 100)) : 0;
    fill.style.width = `${percent}%`;
    label.textContent = `${formatDuration(current)} / ${formatDuration(duration)}`;
    track.setAttribute("aria-valuenow", String(Math.round(percent)));
    toggle.textContent = playback.paused ? "▶" : "Ⅱ";
    toggle.setAttribute("aria-label", playback.paused ? "Play recording" : "Pause recording");
  };

  toggle.addEventListener("click", async () => {
    if (!playback.src) return;
    if (playback.paused) await playback.play();
    else playback.pause();
    update();
  });
  const seek = (event) => {
    const duration = Number.isFinite(playback.duration) && playback.duration > 0 ? playback.duration : 0;
    if (!duration) return;
    const rect = track.getBoundingClientRect();
    const percent = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    playback.currentTime = percent * duration;
    update();
  };
  track.addEventListener("click", seek);
  track.addEventListener("keydown", (event) => {
    const duration = Number.isFinite(playback.duration) && playback.duration > 0 ? playback.duration : 0;
    if (!duration || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Home") playback.currentTime = 0;
    else if (event.key === "End") playback.currentTime = duration;
    else playback.currentTime = Math.min(duration, Math.max(0, playback.currentTime + (event.key === "ArrowRight" ? 5 : -5)));
    update();
  });
  playback.addEventListener("loadedmetadata", update);
  playback.addEventListener("timeupdate", update);
  playback.addEventListener("play", update);
  playback.addEventListener("pause", update);
  playback.addEventListener("ended", update);

  return {
    show(recording) {
      if (!recording?.url) return;
      playback.src = recording.url;
      player.dataset.durationSeconds = String(recording.duration_seconds || 0);
      playback.hidden = true;
      player.hidden = false;
      update();
    },
  };
}

async function uploadSpeakingRecording(question, blob, duration, status, playback) {
  try {
    const params = new URLSearchParams({
      test_id: state.testId,
      question_key: question.key,
      duration_seconds: duration.toFixed(2),
    });
    const response = await fetch(`/api/recordings?${params}`, {
      method: "POST",
      headers: { "Content-Type": blob.type || "audio/webm" },
      body: blob,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    state.answers[question.key] = `recording:${result.recording_id}`;
    persist();
    playback.hidden = true;
    status.textContent = "Response recorded and saved. Continue to the next task.";
    renderStats();
    window.setTimeout(advanceSpeakingTask, 800);
  } catch (error) {
    status.textContent = `Recorded for this page, but local save failed: ${error.message}`;
  }
}

function advanceSpeakingTask() {
  if (state.section !== "speaking" || state.submissions.speaking) return;
  if (state.index < sectionGroups().length - 1) {
    state.index += 1;
    render();
  } else {
    submitSection();
  }
}

function questionFeedback(q) {
  const checked = state.checked[q.key];
  if (checked === undefined || checked === null) return "";
  if (checked) return `<div class="inline-feedback good">Correct.</div>`;
  const correct = q.options.filter((option) => option.is_correct).map((option) => option.text || option.label).join("; ");
  return `<div class="inline-feedback bad">Correct answer: ${escapeHtml(correct)}</div>`;
}

async function renderSource(group, showHidden = false) {
  const page = group.source_file || group.page;
  if (!page) {
    $("sourceContent").innerHTML = "<p>No source page was found for this task.</p>";
    return;
  }
  const cacheKey = `${state.testId}:${page}:${showHidden ? "analysis" : "normal"}`;
  if (!state.sourceCache.has(cacheKey)) {
    try {
      const html = await fetch(sourceUrl(page), { cache: "no-store" }).then((r) => r.text());
      state.sourceCache.set(cacheKey, extractArticle(html, page, showHidden));
    } catch {
      state.sourceCache.set(cacheKey, "<p>Could not load the saved source page.</p>");
    }
  }
  $("sourceContent").innerHTML = state.sourceCache.get(cacheKey);
}

function extractArticle(html, page, showHidden = false) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const body = doc.querySelector('[itemprop="articleBody"]') || doc.querySelector("main") || doc.body;
  body.querySelectorAll("script, style, iframe, form input[type='hidden']").forEach((node) => node.remove());
  if (showHidden) body.querySelectorAll("[hidden]").forEach((node) => node.removeAttribute("hidden"));
  body.querySelectorAll("[src]").forEach((node) => {
    const src = node.getAttribute("src");
    if (!src || /^(https?:)?\/\//.test(src) || src.startsWith("data:")) return;
    node.setAttribute("src", assetUrl(resolveRelativePath(page, src)));
  });
  body.querySelectorAll("[href]").forEach((node) => {
    const href = node.getAttribute("href");
    if (!href || href.startsWith("#") || /^(https?:)?\/\//.test(href)) return;
    node.removeAttribute("href");
  });
  return body.innerHTML.trim() || "<p>This saved page does not include additional source text.</p>";
}

function resolveRelativePath(fromFile, relativePath) {
  if (relativePath.startsWith("/")) return relativePath.replace(/^\/+/, "");
  const parts = fromFile.split("/");
  parts.pop();
  for (const segment of relativePath.split("/")) {
    if (!segment || segment === ".") continue;
    if (segment === "..") parts.pop();
    else parts.push(segment);
  }
  return parts.join("/");
}

async function submitSection() {
  if (state.submittingSection || state.submissions[state.section]) return;
  state.submittingSection = true;
  try {
    const choiceQuestions = sectionChoiceQuestions();
    if (!choiceQuestions.length) {
      stopTimer();
      state.submissions[state.section] = {
        total: sectionQuestions().length,
        correct: null,
        level: null,
        elapsed_seconds: state.timer.elapsed,
        note: state.section === "writing"
          ? "Writing saved. Requesting an AI practice assessment using CELPIP criteria."
          : state.section === "speaking"
            ? "Speaking saved. Requesting an AI practice assessment using CELPIP criteria."
            : "This section has been saved locally.",
        submitted_at: new Date().toISOString(),
      };
      persist();
      if (state.section === "writing") renderFeedback(null, "Writing submitted. AI grading may take up to a minute.");
      if (state.section === "speaking") renderFeedback(null, "Speaking submitted. AI grading may take up to a minute.");
      await saveSubmissionToDatabase();
      await render();
      return;
    }

    const unanswered = choiceQuestions.filter((q) => !state.answers[q.key]);
    if (unanswered.length && !["listening", "reading"].includes(state.section)) {
      renderFeedback(null, `Answer ${unanswered.length} more question${unanswered.length > 1 ? "s" : ""} before submitting this section.`);
      return;
    }

    stopTimer();

    let correct = 0;
    for (const question of choiceQuestions) {
      const selected = state.answers[question.key];
      const option = question.options.find((item) => item.id === selected || item.value === selected);
      const isCorrect = Boolean(option?.is_correct);
      state.checked[question.key] = isCorrect;
      if (isCorrect) correct += 1;
    }

    const level = estimateLevel(state.section, correct, choiceQuestions.length);
    state.submissions[state.section] = {
      total: choiceQuestions.length,
      correct,
      level: level?.level || null,
      elapsed_seconds: state.timer.elapsed,
      note: level
        ? `Practice estimate using the published raw-score range ${level.min}-${level.max}. Official scores can vary by test form.`
        : "Raw practice score only. This section is too short for a CELPIP level estimate.",
      submitted_at: new Date().toISOString(),
    };
    persist();
    await saveSubmissionToDatabase();
    await render();
  } finally {
    state.submittingSection = false;
  }
}

async function saveSubmissionToDatabase() {
  const submission = state.submissions[state.section];
  if (!submission) return;
  if (!SERVER_API_ENABLED) {
    delete submission.db_error;
    delete submission.db_attempt_id;
    delete submission.db_created_at;
    persist();
    return;
  }

  const payload = buildSubmissionPayload(submission);
  try {
    const response = await fetch("/api/submissions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    submission.db_attempt_id = result.attempt_id;
    submission.db_created_at = result.created_at;
    if (result.writing_assessment) {
      submission.writing_assessment = result.writing_assessment;
      submission.level = String(result.writing_assessment.overall_level);
      submission.note = result.writing_assessment.disclaimer;
      delete submission.ai_error;
    } else if (result.writing_assessment_error) {
      submission.ai_error = result.writing_assessment_error;
    }
    if (result.speaking_assessment) {
      submission.speaking_assessment = result.speaking_assessment;
      submission.level = String(result.speaking_assessment.overall_level);
      submission.note = result.speaking_assessment.disclaimer;
      delete submission.ai_error;
    } else if (result.speaking_assessment_error) {
      submission.ai_error = result.speaking_assessment_error;
    }
    delete submission.db_error;
  } catch (error) {
    submission.db_error = error.message || "Could not write SQLite database. Run the app with server.py.";
    delete submission.db_attempt_id;
    delete submission.db_created_at;
  }
  persist();
}

function buildSubmissionPayload(submission) {
  const questions = sectionQuestions();
  const responses = questions.map((question) => {
    const answerValue = state.answers[question.key] ?? null;
    const selectedOption = question.options?.find((option) => option.id === answerValue || option.value === answerValue);
    return {
      question_key: question.key,
      group_id: question.group_id || null,
      question_number: question.number,
      source_file: question.source_pages?.[0]?.file || question.source_file || null,
      answer_value: answerValue,
      answer_text: selectedOption ? (selectedOption.text || selectedOption.media?.[0]?.path || selectedOption.label) : answerValue,
      is_correct: state.checked[question.key] ?? null,
      correct_answers: question.correct_answers || [],
    };
  });

  return {
    test_id: state.testId,
    section: state.section,
    total_questions: questions.length,
    answered_count: questions.filter((question) => state.answers[question.key]).length,
    correct_count: submission.correct,
    estimated_level: submission.level,
    note: submission.note,
    elapsed_seconds: submission.elapsed_seconds,
    submitted_at: submission.submitted_at,
    responses,
  };
}

function estimateLevel(section, correct, total) {
  const table = SCORE_TABLES[section];
  if (!table || !hasOfficialScoreTotal(section, total)) return null;
  return table.find((row) => correct >= row.min && correct <= row.max);
}

function hasOfficialScoreTotal(section, total) {
  const table = SCORE_TABLES[section];
  if (!table) return false;
  return total === Math.max(...table.map((row) => row.max));
}

function displayLevelForResult(section, result) {
  if (!result || !hasOfficialScoreTotal(section, result.total ?? result.total_questions)) return null;
  return result.estimated_level || result.level || null;
}

function renderFeedback(q, message) {
  const box = $("feedback");
  if (!message) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.className = "feedback note";
  box.textContent = message;
}

async function moveQuestion(delta) {
  persist({ sync: false });
  syncDraftToDatabase(state.testId);
  state.index = Math.max(0, Math.min(sectionGroups().length - 1, state.index + delta));
  await render();
  resetPracticeScroll();
}

function resetPracticeScroll() {
  const source = $("sourceContent");
  const questions = document.querySelector(".question-panel");
  if (source) source.scrollTop = 0;
  if (questions) questions.scrollTop = 0;
}

function resetSectionTimer() {
  const section = SECTIONS.find((item) => item.id === state.section);
  const limit = (section?.minutes || 0) * 60;
  const saved = state.timings[state.section] || {};
  state.timer.elapsed = Math.max(0, Number(saved.elapsed_seconds) || 0);
  state.timer.remaining = Math.max(0, Number.isFinite(saved.remaining_seconds) ? saved.remaining_seconds : limit - state.timer.elapsed);
  updateTimer();
}

function toggleTimer() {
  if (state.timer.running) {
    stopTimer();
    return;
  }
  if (state.timer.remaining <= 0) resetSectionTimer();
  state.timer.running = true;
  $("timerBtn").textContent = "Ⅱ";
  state.timer.id = window.setInterval(() => {
    state.timer.remaining = Math.max(0, state.timer.remaining - 1);
    state.timer.elapsed += 1;
    saveCurrentTiming(false);
    if (state.timer.elapsed % 15 === 0) scheduleDraftSync(state.testId, 0);
    updateTimer();
    if (state.timer.remaining === 0) stopTimer();
  }, 1000);
}

function stopTimer() {
  if (state.timer.id) window.clearInterval(state.timer.id);
  state.timer.id = null;
  state.timer.running = false;
  $("timerBtn").textContent = "▶";
  saveCurrentTiming(true);
}

function updateTimer() {
  const timer = document.querySelector(".timer");
  const submission = state.submissions[state.section];
  if (timer) timer.hidden = isIntroSection(state.section) && state.sectionIntro && !submission;
  if (submission) {
    $("timerLabel").textContent = "Time used";
    $("timerValue").textContent = submission.elapsed_seconds === null || submission.elapsed_seconds === undefined
      ? "--:--"
      : formatDuration(submission.elapsed_seconds);
    $("timerBtn").hidden = true;
    return;
  }
  $("timerBtn").hidden = true;
  $("timerLabel").textContent = `${SECTIONS.find((item) => item.id === state.section)?.label || "Practice"} remaining`;
  $("timerValue").textContent = formatDuration(state.timer.remaining);
}

function saveCurrentTiming(sync) {
  if (!state.data || state.submissions[state.section]) return;
  state.timings[state.section] = {
    elapsed_seconds: state.timer.elapsed,
    remaining_seconds: state.timer.remaining,
  };
  localStorage.setItem(timingStorageKey(), JSON.stringify(state.timings));
  if (sync) scheduleDraftSync(state.testId, 0);
}

function formatDuration(totalSeconds) {
  const secondsValue = Math.floor(Math.max(0, Number(totalSeconds) || 0));
  const hours = Math.floor(secondsValue / 3600);
  const minutes = Math.floor((secondsValue % 3600) / 60);
  const seconds = secondsValue % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function toggleSource() {
  const panel = document.querySelector(".source-panel");
  panel.classList.toggle("collapsed");
  $("toggleSourceBtn").textContent = panel.classList.contains("collapsed") ? "Show" : "Hide";
}

function countWords(text) {
  return (text.trim().match(/\b[\w'-]+\b/g) || []).length;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

init().catch((error) => {
  console.error(error);
  $("questionTitle").textContent = "Could not load practice data";
  $("questionText").textContent = error.message;
});
