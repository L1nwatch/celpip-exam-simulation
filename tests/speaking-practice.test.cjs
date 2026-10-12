const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../webapp/app.js'), 'utf8').split('init().catch')[0];
const resultBox = {
  hidden: false, innerHTML: '',
  querySelector() { return null; },
  querySelectorAll() { return []; },
};
const context = vm.createContext({
  document: { getElementById() { return resultBox; } },
});
const run = code => vm.runInContext(code, context);
run(source);
run(`
  state.section = 'speaking';
  state.data = { questions: [
    { key: 'practice', section: 'speaking', number: null, question_text: 'Talk about your hobby.' },
    { key: 'task1', section: 'speaking', number: 1, question_text: 'Give your friend advice.' },
  ] };
  state.submissions.speaking = { db_attempt_id: 42 };
`);

// Reviewing a saved warm-up should show its recording without grading controls.
run('renderSectionResult()');
assert.equal(resultBox.hidden, true);
assert.match(run('renderQuestionCard(sectionQuestions()[0])'), /Optional warm-up.*not graded/);

// Formal tasks retain the section grading action when moving forward.
run('state.index = 1; renderSectionResult()');
assert.equal(resultBox.hidden, false);
assert.match(resultBox.innerHTML, /class="grade-speaking-assessment"/);
assert.doesNotMatch(run('renderQuestionCard(sectionQuestions()[1])'), /Optional warm-up/);

// Old cached assessments must not show a score for the warm-up either.
run(`
  state.submissions.speaking.speaking_assessment = {
    overall_level: 8, summary: 'Clear response.', task_assessments: [
      { question_key: 'practice', task_number: 1, estimated_level: 3,
        transcript: 'Warm-up.', criteria: [], strengths: [], improvements: [] },
      { question_key: 'task1', task_number: 1, estimated_level: 8,
        transcript: 'Formal response.', criteria: [], strengths: [], improvements: [] },
    ],
  };
`);
assert.equal(run('speakingAssessmentHtml(sectionQuestions()[0])'), '');
assert.match(run('speakingAssessmentHtml(sectionQuestions()[1])'), /Estimated Level 8/);
run('state.index = 0; renderSectionResult()');
assert.equal(resultBox.hidden, true);

console.log('Speaking warm-up stays ungraded; formal tasks retain grading and feedback.');
