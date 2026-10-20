const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const listeners={},els={'meta[name="research-token"]':{content:'test'}};
const ctx=vm.createContext({document:{querySelector:s=>els[s],querySelectorAll:()=>[],addEventListener:(name,fn)=>(listeners[name]||=[]).push(fn)},window:{addEventListener:()=>{}},localStorage:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}},setTimeout:()=>1,clearTimeout:()=>{},queueMicrotask:()=>{},URL,Date,Intl,FormData:class{},navigator:{clipboard:{writeText:async()=>{}}},crypto:{randomUUID:()=> 'unique-request-id'},console});
for(const file of ['pathways.js','research.js','plotting.js','app.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../web',file),'utf8').replace(/\binit\(\);\s*$/,''),ctx);
const run=code=>vm.runInContext(code,ctx);
(async()=>{
 run(`S.view='project';S.tab='research';RT.tab='figures';S.catalog={flows:{full:{stages:['research']}},stages:{research:{title:'研究阶段'}}};S.project={id:'p',flow:'full',assets:[{id:'a',name:'<img src=x>.csv',size:80}],research:{figures:[],experiments:[]},plotting:{figures:[]}};render=()=>{renderCount=(typeof renderCount==='undefined'?0:renderCount)+1};toast=()=>{};closeModal=()=>{};modal=html=>lastModal=html;PL=plotState();PL.catalog=plotFallbackCatalog;PL.inspections.a={kind:'table',columns:[{name:'step',type:'numeric'},{name:'loss',type:'numeric'},{name:'method',type:'text'}],row_count:2,preview:[{step:1,loss:0.5,method:'<svg onload=x>'}]};PL.sources=['a'];`);
 let html=run('plottingView(S.project)');assert.ok(html.includes('&lt;img src=x&gt;.csv'));assert.ok(!html.includes('<img src=x>.csv'));assert.ok(html.includes('&lt;svg onload=x&gt;'));assert.ok(html.includes('本机推荐不调用模型'));assert.ok(html.includes('手工图表记录'));
 await run(`plottingAction('pl-new',{dataset:{}})`);assert.equal(run('PL.spec.mapping.y'),'loss');assert.equal(run('PL.spec.style.palette'),'accessible');
 run(`PL.spec.title='Reliable <script>model</script>';PL.spec.diagram={nodes:[],edges:[]};`);assert.ok(!run('plotEditor(PL,S.project)').includes('<script>model'));
 assert.equal(run(`plotKey({...PL.spec,style:{width:7.2,height:4.8,dpi:180,font_size:11,palette:'accessible'}})`),run('plotKey(PL.spec)'),'omitted defaults and object key order must not create phantom unsaved changes');
 run(`plotRemember();PL.spec.title='changed';`);await run(`plottingAction('pl-undo',{dataset:{}})`);assert.equal(run('PL.spec.title'),'Reliable <script>model</script>');await run(`plottingAction('pl-redo',{dataset:{}})`);assert.equal(run('PL.spec.title'),'changed');
 // Local recommendation never opts into a model; explicit AI requests carry an idempotency ID.
 els['#pl-purpose']={value:'Compare accuracy versus latency'};
 run(`api=async(path,data)=>{lastRequest={path,data};return {recommendations:[{kind:'scatter',reason:'Two measured variables',spec:plotNewSpec('scatter',['a'])}],warnings:[]}}`);
 await run(`plottingAction('pl-recommend',{dataset:{}})`);assert.equal(run('lastRequest.data.use_ai'),false);assert.equal(run('lastRequest.data.request_id'),undefined);
 await run(`plottingAction('pl-recommend-ai',{dataset:{}})`);assert.equal(run('lastRequest.data.use_ai'),true);assert.equal(run('lastRequest.data.request_id'),'unique-request-id');
 // Unsaved replacement requires an explicit decision and does not mutate the current recipe.
 const before=run('plotKey(PL.spec)');await run(`plottingAction('pl-new-diagram',{dataset:{}})`);assert.equal(run('plotKey(PL.spec)'),before);assert.ok(run(`lastModal.includes('有未保存修改')`));
 els['#pl-instruction']={value:'Use a restrained palette'};
 run(`api=async(path,data)=>{lastRequest={path,data};return {spec:{...plotClone(PL.spec),title:'AI candidate'},explanation:'Change title only',warnings:[]}}`);
 await run(`plottingAction('pl-ai',{dataset:{}})`);assert.equal(run('PL.spec.title'),'changed');assert.equal(run('PL.candidate.spec.title'),'AI candidate');assert.equal(run('lastRequest.data.request_id'),'unique-request-id');assert.ok(run('plotEditor(PL,S.project)').includes('<details open><summary>让 AI 调整'));
 await run(`plottingAction('pl-apply-ai',{dataset:{}})`);assert.equal(run('PL.spec.title'),'AI candidate');assert.equal(run('PL.candidate'),null);
 // A late AI proposal cannot overwrite edits made while the request was in flight.
 run(`api=(path,data)=>new Promise(resolve=>{finishAI=()=>resolve({spec:{...data.spec,title:'late AI'},explanation:'late'})})`);
 const ai=run(`plottingAction('pl-ai',{dataset:{}})`);await new Promise(resolve=>setImmediate(resolve));run(`PL.spec.title='manual change';finishAI()`);await assert.rejects(ai,/图表已修改/);assert.equal(run('PL.spec.title'),'manual change');
 // Saving updates uses the last server revision and keeps new typing after a pending response.
 run(`S.project.plotting.figures=[{id:'f',revision:3,title:'server',spec:plotClone(PL.spec),caption:'',history:[]}];PL.id='f';api=(path,data)=>{if(path==='projects')return Promise.resolve([]);lastSave=data;return new Promise(resolve=>{finishSave=()=>resolve({...S.project,plotting:{figures:[{id:'f',revision:4,title:data.spec.title,spec:data.spec,caption:'',history:[]}]}})})}`);
 const save=run('plotSave(PL)');await new Promise(resolve=>setImmediate(resolve));assert.equal(run('lastSave.expected_revision'),3);run(`PL.spec.title='typed during save';finishSave()`);await save;assert.equal(run('PL.spec.title'),'typed during save');assert.equal(run('S.project.plotting.figures[0].revision'),4);
 // Render requests are tied to an editing session, so a late preview cannot replace a new chart.
 run(`api=()=>new Promise(resolve=>{finishRender=()=>resolve({preview:'bGF0ZQ==',caption:'late',warnings:[]})});PL.preview=null`);
 const preview=run('plotRender(PL)');await new Promise(resolve=>setImmediate(resolve));run(`PL.editSession++;finishRender()`);await preview;assert.equal(run('PL.preview'),null);
 // No generated image is sent from the browser to AI, and exported names are escaped at rendering.
 assert.ok(!run('JSON.stringify(lastRequest.data)').includes('<svg onload=x>'));
 const diff=JSON.parse(run(`JSON.stringify(plotDiff({title:'a',style:{palette:'muted'}},{title:'b',style:{palette:'gray'}}))`));assert.deepEqual(diff,['标题：a → b','样式 · 配色：克制多色 → 灰阶']);
 run(`api=async()=>({preview:'cHJldmlldw==',warnings:[],caption:'Draft'})`);await run(`plotOpenFigure('f',PL)`);assert.equal(run('PL.sourcesExpanded'),false);assert.equal(run('PL.fieldsExpanded'),false);assert.equal(run('PL.styleExpanded'),false);assert.equal(run('plotDirty(PL)'),false);
 // Oversize captions remain intact and are rejected before any request, including pasted legacy drafts.
 assert.ok(run('plotMetadata(PL,null)').includes('maxlength="1400"'));assert.ok(run('plotMetadata(PL,null)').includes('不会自动截断'));
 run(`PL.meta.caption='注'.repeat(1401);PL.captionEdited=true;api=async()=>{throw Error('must not request')}`);
 await assert.rejects(run('plotSave(PL)'),/最多 1400 字/);assert.equal(run('PL.meta.caption.length'),1401);run(`PL.meta.caption='';PL.captionEdited=false`);
 // Opening from a lower card waits for the final preview and its intrinsic image dimensions.
 const positions=[],frames=[];let finishDecode;const image={decode:()=>new Promise(resolve=>{finishDecode=resolve})};
 els['.pl-editor']={querySelector:()=>image,scrollIntoView:options=>positions.push(options)};ctx.requestAnimationFrame=fn=>(frames.push(fn),frames.length);
 run(`api=()=>new Promise(resolve=>{finishOpenPreview=()=>resolve({preview:'cHJldmlldw==',warnings:[],caption:'Draft'})})`);
 const opening=run(`plotOpenFigure('f',PL)`);await new Promise(resolve=>setImmediate(resolve));assert.equal(positions.length,0,'do not scroll an element that plotRender will replace');
 run('finishOpenPreview()');await new Promise(resolve=>setImmediate(resolve));assert.equal(positions.length,0,'wait for the preview image to decode');finishDecode();await new Promise(resolve=>setImmediate(resolve));assert.equal(frames.length,1);assert.equal(positions.length,0,'wait for the final layout frame');frames.shift()();await opening;
 assert.equal(positions.length,1);assert.equal(positions[0].block,'start');assert.equal(positions[0].behavior,'instant');
 // Regular redraws preserve the reader's scroll position; delayed opening focus obeys navigation.
 run(`api=async()=>({preview:'cHJldmlldw==',warnings:[],caption:'Draft'})`);await run('plotRender(PL)');assert.equal(positions.length,1);assert.equal(frames.length,0);
 const focus=run('plotFocusEditor(PL,PL.editSession)');finishDecode();await new Promise(resolve=>setImmediate(resolve));run(`S.tab='materials'`);frames.shift()();await focus;assert.equal(positions.length,1,'leaving the drawing page must cancel pending focus');
 console.log('Plotting UI checks passed: escaping, field mapping, local recommendations, explicit AI proposals, idempotency IDs, edit preservation, revision saves, late preview isolation and undo/redo.');
})().catch(error=>{console.error(error);process.exitCode=1});
