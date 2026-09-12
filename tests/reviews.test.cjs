const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../webapp/app.js'), 'utf8').split('init().catch')[0];

function setup(serverEnabled, storage = new Map()) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      hidden: false, attributes: {}, textContent: '', innerHTML: '',
      setAttribute(key, value) { this.attributes[key] = value; },
      querySelectorAll() { return []; },
    });
    return elements.get(id);
  };
  const requests = [];
  const saved = new Map();
  const context = vm.createContext({
    document: { getElementById: element },
    window: { history: { replaceState() {} }, location: { pathname: '/webapp/index.html' } },
    localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    fetch: async (url, options = {}) => {
      if (url === '/api/reviews') {
        if (options.method === 'POST') {
          const review = JSON.parse(options.body);
          requests.push(review);
          const key = JSON.stringify([review.test_id, review.section]);
          if (review.reviewed) saved.set(key, review);
          else saved.delete(key);
        }
        return { ok: true, json: async () => ({ reviews: [...saved.values()] }) };
      }
      return { ok: true, json: async () => ({ attempts: [], drafts: [] }) };
    },
  });
  vm.runInContext(source.replace('const SERVER_API_ENABLED = true;', `const SERVER_API_ENABLED = ${serverEnabled};`), context);
  const run = code => vm.runInContext(code, context);
  run(`
    state.data = { questions: [], question_groups: { listening: [
      { source_file: 'pages/part1.html', title: 'Part 1', question_keys: [] },
      { source_file: 'pages/part2.html', title: 'Part 2', question_keys: [] },
    ] } };
    stopTimer = () => {};
    stopPracticePlayback = () => {};
    stopListeningQuestionTimer = () => {};
  `);
  return { run, element, context, requests, saved, storage };
}

(async () => {
  for (const serverEnabled of [true, false]) {
    const app = setup(serverEnabled);
    const { run, element } = app;
    await run('loadReviews()');
    run('renderReviewControl()');
    assert.equal(element('reviewBtn').attributes['aria-pressed'], 'false');
    await run('toggleReviewed()');
    assert.equal(element('reviewBtn').textContent, '★ Listening reviewed');
    run('renderQuestionNav(sectionGroups())');
    assert.doesNotMatch(element('questionNav').innerHTML, /Reviewed/);
    run('state.index = 1; renderReviewControl()');
    assert.equal(element('reviewBtn').attributes['aria-pressed'], 'true');
    assert.equal(run("isSectionReviewed(state.testId, 'reading')"), false);
    assert.equal(run("isSectionReviewed('local_celpip1_test2', state.section)"), false);
    await run('showOverview()');
    assert.equal(element('reviewBtn').hidden, true);
    assert.equal((element('overviewBody').innerHTML.match(/class="review-star"/g) || []).length, 1);
    assert.match(element('overviewBody').innerHTML, />Reviewed</);
    assert.doesNotMatch(element('overviewBody').innerHTML, /parts? reviewed/);
    assert.match(element('overviewBody').innerHTML, /Not started/);
    // A fresh client (or fresh page in preview) reads the persisted marks.
    const next = setup(serverEnabled, app.storage);
    if (serverEnabled) for (const [key, value] of app.saved) next.saved.set(key, value);
    await next.run('loadReviews()');
    assert.equal(next.run('isSectionReviewed()'), true);
    // Unmark from a different part; every part in this section is now unmarked.
    next.run('state.index = 1');
    await next.run('toggleReviewed()');
    await next.run('showOverview()');
    assert.doesNotMatch(next.element('overviewBody').innerHTML, /review-star/);
    next.run('state.index = 0');
    assert.equal(next.run('isSectionReviewed()'), false);
    next.run("state.testId = 'local_celpip1_test2'");
    assert.equal(next.run('isSectionReviewed()'), false);
  }

  const legacy = setup(false, new Map([['celpip-practice:reviewed-pages', JSON.stringify({
    first: {test_id: 'local_celpip1_test1', section: 'listening', page: 'part1.html'},
    second: {test_id: 'local_celpip1_test1', section: 'listening', page: 'part2.html'},
  })]]));
  await legacy.run('loadReviews()');
  assert.equal(legacy.run('Object.keys(state.reviews).length'), 1);
  await legacy.run('toggleReviewed()');
  await legacy.run('loadReviews()');
  assert.equal(legacy.run('isSectionReviewed()'), false);

  const failed = setup(true);
  failed.context.fetch = async () => ({ ok: false, status: 503 });
  await failed.run('toggleReviewed()');
  assert.equal(failed.element('reviewBtn').attributes['aria-pressed'], 'false');
  assert.match(failed.element('reviewNotice').textContent, /Could not save/);
  assert.equal(failed.element('reviewBtn').disabled, false);
  await failed.run('loadReviews();');
  failed.run('renderReviewControl()');
  assert.equal(failed.element('reviewBtn').disabled, true);
  assert.match(failed.element('reviewNotice').textContent, /Reload to retry/);

  const pending = setup(true);
  let finish;
  pending.context.fetch = () => new Promise(resolve => { finish = resolve; });
  const saving = pending.run('toggleReviewed()');
  await Promise.resolve();
  assert.equal(pending.element('reviewBtn').disabled, true);
  await pending.run('toggleReviewed()');
  pending.run('state.index = 1');
  finish({ ok: true });
  await saving;
  assert.equal(pending.element('reviewBtn').attributes['aria-pressed'], 'true');
  pending.run('state.index = 0');
  assert.equal(pending.run('isSectionReviewed()'), true);
  console.log('Review persistence, overview, navigation, failure and pending-save checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
