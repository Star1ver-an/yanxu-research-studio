const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const events={},els={'meta[name="research-token"]':{content:'qa'}};
const ctx=vm.createContext({document:{querySelector:s=>els[s],getElementById:()=>null,addEventListener:(t,f)=>(events[t]||=[]).push(f),body:{classList:{toggle:()=>{}}}},window:{history:{pushState:()=>{}},innerWidth:1000,innerHeight:700},crypto:require('node:crypto').webcrypto,setTimeout:()=>1,clearTimeout:()=>{},URL,Date,Intl,console,structuredClone});
for(const name of ['pathways.js','assistant.js','assistant-proposals.js','app.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../web',name),'utf8').replace(/\binit\(\);\s*$/,''),ctx);
const run=s=>vm.runInContext(s,ctx);
(async()=>{
 run(`a='a'.repeat(32);b='b'.repeat(32);pageA={id:a,title:'RAG',flow:'full',route:'ars',runs:[]};pageB={id:b,title:'模型压缩',flow:'scientific_data',route:'scientific',runs:[]};S.view='project';S.project=pageB;S.tab='work';S.activeStage='kd_scope';S.catalog={};AS.open=true;AS.scope=a;AS.project=pageA;AS.state={messages:[],stage:'research'};AS.drafts[a]='A 的草稿';AS.drafts[b]='B 的草稿';AS.options[a]={report:true,history:false,memory:true,materials:['asset-a']};AS.stages[a]='research';calls=[];
 api=async(path,data)=>{calls.push({path,data});if(path.startsWith('project/'))return path.endsWith(a)?pageA:pageB;if(path==='assistant/catalog')return {scopes:[{id:'',title:'工作区帮助'},{id:a,title:'RAG',lifecycle:'active'},{id:b,title:'模型压缩',lifecycle:'active'}],jobs:[]};return {title:data.project===a?'RAG':'模型压缩',messages:[],stage:data.project===a?'research':'kd_scope',materials:[{id:'asset-a'}],report_available:true}};`);
 assert.equal(run('assistantData().project'),run('a'));assert.equal(run('assistantData().stage'),'research');
 await run('assistantSync()');assert.equal(run('AS.scope'),run('a'));assert.equal(run('S.project.id'),run('b'));
 await run('assistantSelectScope(b)');assert.equal(run('S.project.id'),run('b'));assert.equal(run('S.tab'),'work');assert.equal(run('AS.drafts[AS.scope]'),'B 的草稿');
 await run(`assistantSelectScope('')`);assert.equal(run('S.project.id'),run('b'));assert.equal(run('assistantData().project'),'');assert.equal(run('assistantData().view'),'home');
 await run('assistantSelectScope(a)');assert.equal(run('assistantOptions().report'),true);assert.equal(run('assistantOptions().history'),false);assert.equal(run('AS.drafts[a]'),'A 的草稿');
 // A recheck pins its original stage even when the page shows another stage of that project.
 run(`S.project=pageA;S.activeStage='draft';AS.stageOverrides[a]='research'`);assert.equal(run('assistantData().stage'),'research');
 await run(`assistantAction('as-cancel-recheck',{})`);assert.equal(run('assistantData().stage'),'draft');
 // Local entry questions send once without an AI confirmation; real AI questions stop at the inline preview.
 run(`AS.catalog=null;AS.state={messages:[]};AS.drafts[a]='绘图在哪里';AS.epoch=10;calls=[];api=async(path,data)=>{calls.push({path,data});return path==='assistant/preview'?{local:true,calls:0,sha256:''}:{messages:[],job:null}}`);
 await run('assistantPreview()');assert.deepEqual(Array.from(run('calls.map(c=>c.path)')),['assistant/preview','assistant/send']);
 run(`AS.drafts[a]='请评估实验';calls=[];api=async(path,data)=>{calls.push({path,data});return {sha256:'review',calls:1,sources:[]}}`);
 await run('assistantPreview()');assert.equal(run('calls.length'),1);assert.equal(run('AS.preview.sha256'),'review');assert.equal(run('AS.drafts[a]'),'请评估实验');
 // Server owned targets choose the exact drawing mode, preserve the selected workspace chat, and update both routes.
 run(`AS.scope='';AS.project=null;AS.state={messages:[]};S.draft=null;S.settingsDirty=false;S.project=pageA;S.activeStage='research';RT={tab:'navigator'};plotModes={};plotState=p=>plotModes[p.id]||(plotModes[p.id]={});S.catalog={flows:{full:{stages:['research'],route:'ars'},scientific_data:{stages:['kd_scope'],route:'scientific'}}};pageA.cursor=0;pageB.cursor=0;render=()=>{};recoverOutputDraft=()=>{};schedulePoll=()=>{};calls=[];
 api=async(path,data)=>{calls.push({path,data});if(path==='assistant/navigate')return {project:true,project_id:data.project,tab:'research',tool:'figures',mode:data.target==='paper'?'paper':data.target==='analysis'?'analysis':'draw'};return data===undefined?(path.endsWith(a)?pageA:pageB):{}};`);
 for(const target of ['drawing','analysis','paper']){await run(`assistantNavigate('${target}',b)`);assert.equal(run('S.project.id'),run('b'));assert.equal(run('AS.scope'),'');assert.equal(run('RT.tab'),'figures');assert.equal(run('plotModes[b].mode'),target==='drawing'?'draw':target);}
 // Unsaved settings and failed report saves must block navigation before any route read.
 run(`S.settingsDirty=true;calls=[]`);await assert.rejects(run(`assistantNavigate('drawing',a)`),/设置有未保存/);assert.equal(run('calls.length'),0);
 run(`S.settingsDirty=false;S.draft='未保存';saveOutput=async()=>{}`);await assert.rejects(run(`assistantNavigate('drawing',a)`),/正文修改/);assert.equal(run('calls.length'),0);
 // A page changed during the destination request must not be overwritten by that late response.
 run(`S.draft=null;api=()=>new Promise(resolve=>{resolveRoute=resolve});pendingNav=assistantNavigate('drawing',a);S.tab='history';resolveRoute({project:true,project_id:a,tab:'research',tool:'figures',mode:'draw'})`);await run('pendingNav');assert.equal(run('S.tab'),'history');assert.equal(run('S.project.id'),run('b'));
 // Completion while minimized marks the origin conversation unread and stops polling once all jobs finish.
 run(`AS.open=false;AS.scope=a;AS.state={messages:[],job:{id:'old-job',progress:'running'}};AS.catalog={scopes:[],jobs:[{project:a}]};AS.seen[a]='old';api=async()=>({scopes:[{id:a,title:'RAG',latest:'new'}],jobs:[]});`);
 await run('assistantCatalogRefresh()');assert.equal(run('AS.unread[a]'),true);assert.equal(run('AS.state.job'),null);
 // Dragged windows are clamped into the current viewport after resizing.
 els['.assistant-panel']={getBoundingClientRect:()=>({width:420,height:580}),style:{}};
 run(`AS.position={x:9999,y:-100};assistantFit()`);assert.equal(run('AS.position.x'),568);assert.equal(run('AS.position.y'),12);
 // Review forms use the selected chat project, even while the page shows another project.
 run(`AS.scope=a;AS.project=pageA;S.project=pageB;AS.review={project:pageA};S.catalog.stages={research:{title:'研究'},kd_scope:{title:'范围'}};rtLabels={stage:'阶段'};`);
 const html=run(`proposalField('stage','research',0)`);assert.ok(html.includes('value="research"'));assert.ok(!html.includes('value="kd_scope"'));
 console.log('Assistant navigation UI checks passed: independent scopes, drafts, references, stage rechecks, local/AI preview, exact routes, unsaved edits, late responses, background completion and bounded geometry.');
})().catch(error=>{console.error(error);process.exitCode=1});
