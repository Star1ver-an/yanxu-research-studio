const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const listeners={},timers=[],elements={'meta[name="research-token"]':{content:'test'}};
const ctx=vm.createContext({L:value=>value,H:(strings,...values)=>strings.map((part,i)=>part+(i<values.length?values[i]:'')).join(''),document:{querySelector:s=>elements[s],querySelectorAll:()=>[],addEventListener:(name,fn)=>(listeners[name]||=[]).push(fn)},window:{addEventListener:()=>{}},localStorage:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}},setTimeout:fn=>(timers.push(fn),timers.length),clearTimeout:()=>{},queueMicrotask:()=>{},URL,Date,Intl,FormData:class{},navigator:{clipboard:{writeText:async()=>{}}},crypto:{randomUUID:()=> 'paper-receipt-123456789'},console});
for(const file of ['pathways.js','research.js','plotting.js','analysis.js','paper.js','app.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../web',file),'utf8').replace(/\binit\(\);\s*$/,''),ctx);
const run=code=>vm.runInContext(code,ctx),turn=()=>new Promise(resolve=>setImmediate(resolve));
const change=async value=>{for(const fn of listeners.change||[])await fn({target:{closest:s=>s==='.pf-workspace',tagName:'INPUT',dataset:{},...value}})};
(async()=>{
 run(`S.view='project';S.tab='research';RT.tab='figures';S.catalog={flows:{full:{stages:['research']}},stages:{research:{title:'研究方案'}}};S.project={id:'p',title:'Synthetic AI paper',flow:'full',assets:[{id:'a',name:'<svg onload=bad>.csv',size:90}],outputs:{research:{text:'Synthetic proposal'}},plotting:{figures:[{id:'f',title:'<img onerror=bad>',caption:'<script>bad</script>',revision:1,spec:{kind:'line',source_ids:['a'],style:{width:6,height:4,dpi:100,font_size:10,palette:'accessible'}}}]}};render=()=>renderCount=(typeof renderCount==='undefined'?0:renderCount)+1;toast=()=>{};closeModal=()=>{};modal=html=>lastModal=html;plotState().mode='paper';PF=paperState();V={revision:1,etag:'basis-1',plan:{title:'AI paper',entries:[{figure_id:'f',include:true,role:'main',section:'',caption_reviewed:false}],stages:['research'],requirements:{max_width_mm:180,max_height_mm:230,min_font_pt:7,min_dpi:300,formats:['png','svg']}},audit:{issues:[{level:'warning',kind:'caption_review',message:'<svg onload=bad>',figure_id:'f'}],checks:[],links:[],affected_figures:['f'],errors:0,warnings:1}};paperAdopt(PF,V);`);
 let html=run('paperView(S.project)');assert.ok(html.includes('整篇论文图表'));assert.ok(html.includes('&lt;img onerror=bad&gt;'));assert.ok(!html.includes('<img onerror=bad>'));assert.ok(!html.includes('<script>bad'));
 assert.ok(run("analysisModeSwitch('paper')").includes('aria-pressed="true"'));
 run("PF.selected=['f','foreign-project-figure']");run('paperView(S.project)');assert.equal(run('JSON.stringify(PF.selected)'),'["f"]');
 run("PF.section='check'");html=run('paperView(S.project)');assert.ok(html.includes('&lt;svg onload=bad&gt;'));assert.ok(html.includes('不代表任何期刊'));
 // Entering the view does not install packages, run a model, render data or start a task.
 run('requests=[];api=async(path,data)=>{requests.push({path,data});return V}');await run('paperLoad(PF)');assert.equal(run('requests.length'),1);assert.equal(run('requests[0].path'),'project/p/paper-state');
 // A failed initial load is explicit and is not queued again on every render.
 run("PF.value=null;PF.plan=null;api=async()=>{throw Error('offline')}");await run('paperLoad(PF)');assert.ok(run('PF.attempted'));assert.equal(run('PF.error'),'offline');run('paperAdopt(PF,V)');
 // A late read cannot overwrite a field changed while the request is outstanding.
 run('api=()=>new Promise(resolve=>finishLoad=resolve)');const reading=run('paperLoad(PF)');await turn();run("PF.plan.title='New edit';paperInvalidate(PF);finishLoad({...V,plan:{...V.plan,title:'Old fetched title'}})");await reading;assert.equal(run('PF.plan.title'),'New edit');
 // A late save preserves typing after submission, while adopting its own new server revision.
 run('api=()=>new Promise(resolve=>finishSave=resolve)');const saving=run('paperEnsureSaved(PF)');await turn();run("PF.plan.title='Typed after save';paperInvalidate(PF);finishSave({...V,revision:1,plan:{...V.plan,title:'New edit'}})");await saving;assert.equal(run('PF.plan.title'),'Typed after save');assert.equal(run('PF.value.revision'),1);assert.ok(run('paperDirty(PF)'));
 run('paperAdopt(PF,V);PF.section="style";PF.selected=["f"];requests=[];api=async(path,data)=>{requests.push({path,data});if(path.endsWith("paper-batch-preview"))return {etag:"basis-1",updates:[{id:"f",title:"Synthetic",revision:1,changed_fields:["style"],before_style:{},after_style:{}}],skipped:[],note:"Review before starting"};return V}');
 await run("paperAction('pf-batch-preview',{dataset:{}})");assert.equal(run('requests.length'),1);assert.ok(run('PF.preview'));assert.ok(!run('requests.some(r=>/start|model|connection/.test(r.path))'));
 await change({dataset:{paperStyle:'palette'},tagName:'SELECT',value:'gray'});assert.equal(run('PF.preview'),null);assert.equal(run('PF.style.palette'),'gray');
 // The whole-paper workflow does not mutate analysis columns or author confirmations.
 run('PF.preview={etag:"basis-1",request:paperBatchData(PF),updates:[],skipped:[]};attempts=[];failed=false;api=async(path,data)=>{if(path.endsWith("paper-batch-start")){attempts.push(data.request_id);if(!failed){failed=true;throw Error("reset after submission")}return {id:"job",kind:"batch",status:"running"}}if(path.endsWith("paper-job"))return {id:"job",kind:"batch",status:"cancelled",progress:"cancelled"};throw Error(path)}');
 await assert.rejects(run("paperAction('pf-batch-start',{dataset:{}})"),/reset/);ctx.crypto.randomUUID=()=> 'another-receipt-123456';await run("paperAction('pf-batch-start',{dataset:{}})");assert.equal(run('attempts[0]'),run('attempts[1]'));
 // A completion arriving after navigation cannot replace another project's state.
 run('PF.job={id:"late",kind:"batch",status:"running"};api=async(path)=>{if(path.endsWith("paper-job"))return {id:"late",kind:"batch",status:"completed",summary:{updated:1}};if(path==="project/p")return {id:"p",plotting:{figures:[]}};return V};S.project={id:"other",plotting:{figures:[]}}');
 await run('paperPoll(PF)');assert.equal(run('S.project.id'),'other');
 // Existing uncommitted drawing work is never discarded to create a composition.
 run('S.project={id:"p",plotting:{figures:[]},assets:[]};plotState().spec=plotNewSpec();plotState().spec.title="Unsaved original"');
 await assert.rejects(run('paperAdoptDraft(PF,{kind:"graphical_abstract"})'),/未保存/);assert.equal(run('plotState().spec.title'),'Unsaved original');
 run('PF.job={id:"old-export",kind:"export",status:"completed",stale:true,progress:"可以下载"};PF.value.audit.warnings=1;PF.value.audit.errors=0');
 assert.match(run('paperJobView(PF)'),/交付需重新生成/);assert.doesNotMatch(run('paperJobView(PF)'),/可以下载/);
 assert.doesNotMatch(run('paperDeliverView(PF)'),/下载论文图表交付包/);
 run('PF.pending={key:"export",id:"receipt"};PF.job={id:"export",kind:"export",status:"running"};api=async()=>({id:"export",kind:"export",status:"completed",progress:"已完成"})');
 await run('paperPoll(PF)');assert.equal(run('PF.pending'),null,'完成后主动重新生成必须使用新回执');
 console.log('Paper UI checks passed: escaping, scoped reads, no implicit work, failed-load protection, late read/save preservation, explicit batch preview, idempotent starts, project isolation, stale delivery and drawing-draft protection.');
})().catch(error=>{console.error(error);process.exitCode=1});
