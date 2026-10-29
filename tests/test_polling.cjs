const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8')
  .replace(/\binit\(\);\s*$/, '');

function harness() {
  const timers = new Map(), calls = [], toasts = [], activities = [];
  let nextTimer = 0, renders = 0, respond;
  const context = vm.createContext({
    document: {querySelector: () => ({content: 'synthetic-token'}), addEventListener() {}},
    window: {},
    setTimeout(callback) {const id = ++nextTimer; timers.set(id, callback); return id;},
    clearTimeout(id) {timers.delete(id);},
    mockApi: (...args) => {calls.push(args[0]); return respond(...args);},
    mockToast: (...args) => toasts.push(args),
    mockRender: () => renders++,
    mockActivity: html => activities.push(html),
    L: value => value,
  });
  const run = code => vm.runInContext(code, context);
  vm.runInContext(source, context);
  run(`S.view='project';S.project={id:'original',runs:[{status:'running'}]};S.tab='work';
       api=mockApi;toast=mockToast;render=mockRender;updateActivity=mockActivity;
       activityView=p=>p.runs[0].progress||'';`);
  return {
    run, timers, calls, toasts, activities,
    respondWith(handler) {respond = handler;},
    renders: () => renders,
    async tick() {
      assert.equal(timers.size, 1);
      const [id, callback] = timers.entries().next().value;
      timers.delete(id);
      await callback();
    },
  };
}

(async () => {
  const recovery = harness();
  recovery.respondWith(async () => {throw Error('temporary connection failure');});
  recovery.run('schedulePoll()');
  await recovery.tick();
  assert.equal(recovery.timers.size, 1, 'a failed request must schedule a retry');
  assert.equal(recovery.toasts[0][1], true);
  recovery.respondWith(async () => ({id: 'original', runs: [{status: 'running', progress: 'resumed'}]}));
  await recovery.tick();
  assert.deepEqual(recovery.activities, ['resumed']);
  assert.equal(recovery.timers.size, 1);
  recovery.respondWith(async route => route === 'projects' ? [] : {id: 'original', runs: [{status: 'completed'}]});
  await recovery.tick();
  assert.equal(recovery.timers.size, 0, 'completed runs must stop polling');
  assert.equal(recovery.renders(), 1);

  const listFailure = harness();
  listFailure.respondWith(async route => {
    if (route === 'projects') throw Error('temporary list failure');
    return {id: 'original', runs: [{status: 'completed'}]};
  });
  listFailure.run('schedulePoll()');
  await listFailure.tick();
  assert.equal(listFailure.timers.size, 1, 'a failed completion refresh must also retry');
  listFailure.respondWith(async route => route === 'projects' ? [] : {id: 'original', runs: [{status: 'completed'}]});
  await listFailure.tick();
  assert.equal(listFailure.timers.size, 0);
  assert.equal(listFailure.renders(), 1);

  const left = harness();
  left.run("schedulePoll();S.view='home';");
  await left.tick();
  assert.equal(left.calls.length, 0, 'leaving a project must cancel work before the timer fires');
  assert.equal(left.timers.size, 0);

  for (const outcome of ['success', 'failure']) {
    const switched = harness();
    let resolve, reject;
    switched.respondWith(() => new Promise((yes, no) => {resolve = yes; reject = no;}));
    switched.run('schedulePoll()');
    const pending = switched.tick();
    switched.run("S.project={id:'other',runs:[{status:'running'}]};schedulePoll();");
    const otherTimer = [...switched.timers.keys()];
    if (outcome === 'success') resolve({id: 'original', runs: [{status: 'completed'}]});
    else reject(Error('late failure for original project'));
    await pending;
    assert.equal(switched.run('S.project.id'), 'other');
    assert.deepEqual([...switched.timers.keys()], otherTimer, 'old callbacks must preserve the new project timer');
    assert.equal(switched.toasts.length, 0, 'old callbacks must not show messages in another project');
    assert.equal(switched.renders(), 0);
  }

  const lateList = harness();
  let finishList, markListRequested;
  const listRequested = new Promise(resolve => {markListRequested = resolve;});
  lateList.respondWith(route => route === 'projects'
    ? new Promise(resolve => {finishList = resolve; markListRequested();})
    : Promise.resolve({id: 'original', runs: [{status: 'completed'}]}));
  lateList.run('schedulePoll()');
  const pendingList = lateList.tick();
  await listRequested;
  lateList.run("S.project={id:'other',runs:[{status:'running'}]};schedulePoll();");
  const otherTimer = [...lateList.timers.keys()];
  finishList([]);
  await pendingList;
  assert.equal(lateList.run('S.project.id'), 'other');
  assert.deepEqual([...lateList.timers.keys()], otherTimer);
  assert.equal(lateList.renders(), 0);
  assert.equal(lateList.toasts.length, 0);

  console.log('Polling regression checks passed: retries, recovery, completion, navigation and late response isolation.');
})().catch(error => {console.error(error); process.exitCode = 1;});
