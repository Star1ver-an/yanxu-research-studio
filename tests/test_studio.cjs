const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const storage=new Map(),elements={},events={};let modalHTML='',opened=[],calls=[],rendered=0;
const id='a'.repeat(32);
const ctx=vm.createContext({
 L:s=>s,H:(parts,...values)=>parts.map((p,i)=>p+(values[i]??'')).join(''),
 e:s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
 icon:()=>'',button:(a,label,k,i,attrs='')=>`<button data-action="${a}" ${attrs}>${label}</button>`,
 activeProjects:()=>ctx.S.projects,date:()=> 'today',routeCards:()=>'<div class="route-cards"></div>',
 S:{projects:[],project:null,tab:'overview'},
 $:s=>elements[s]||null,document:{addEventListener:(name,fn)=>events[name]=fn,createElement:()=>({click(){}})},
 localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,val)=>storage.set(key,val),removeItem:key=>storage.delete(key)},
 modal:html=>modalHTML=html,closeModal:()=>vm.runInContext('cacheStudyDraft()',ctx),
 api:async(p,data)=>{calls.push([p,data]);return p==='projects'?ctx.S.projects:ctx.response},
 assistantNavigate:async(target,pid)=>opened.push([target,pid]),
 openProject:async(pid,tab)=>opened.push([pid,tab]),render:()=>rendered++,toast(){},setTimeout(){},URL:{createObjectURL:()=>'',revokeObjectURL(){}},
 newProjectModal(){}
});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/studio.js'),'utf8'),ctx);
const run=s=>vm.runInContext(s,ctx);
(async()=>{
 assert.match(run('studioHome()'),/创建研究项目/);
 const fields=Object.fromEntries(Object.keys(run('studyFields')).map(k=>[k,k==='working_title'?'Safe </textarea><img src=x onerror=alert(1)>':'Draft <script>alert(1)</script>']));
 const project={id,title:'<img src=x>',asset_count:0,flow:'full',updated:'today',study_status:'draft',study_brief:{fields,revision:1,profile:'finance',status:'draft',context_enabled:false}};
 ctx.S.projects=[project];ctx.S.project=project;
 assert.ok(!run('studioHome()').includes('<img src=x>'));
 assert.match(run('studioHome()'),/简报待核对/);
 assert.ok(!run('studyBriefView(S.project)').includes('<script>'));
 run('studioEdit()');assert.ok(!modalHTML.includes('</textarea><img'));
 await run(`studioTask('analysis','${id}')`);assert.match(modalHTML,/专用金融回测引擎尚未接入/);assert.equal(opened.length,0);
 await run(`studioTask('analysis','${id}',true)`);assert.deepEqual(opened.at(-1),['analysis',id]);
 const form={dataset:{project:id,revision:'1'},elements:Object.fromEntries(Object.keys(fields).map(k=>[k,{value:fields[k]}])),querySelectorAll:()=>[],isConnected:true};
 form.elements.context_enabled={checked:false};form.elements.acknowledge={checked:false};elements['#studio-brief-form']=form;
 assert.equal(run('cacheStudyDraft()'),true);
 assert.ok(storage.has('yanxu-study-draft:'+id));assert.ok(!storage.has('yanxu-study-draft:'+'b'.repeat(32)));
 await assert.rejects(run('studioSave($("#studio-brief-form"),true)'),/勾选/);
 ctx.api=async()=>{throw Error('offline')};
 await assert.rejects(run('studioSave($("#studio-brief-form"))'),/offline/);
 assert.equal(run('Studio.saving'),false);assert.ok(storage.has('yanxu-study-draft:'+id));
 ctx.response=project;ctx.api=async(p,data)=>{calls.push([p,data]);return p==='projects'?[project]:project};
 await run('studioSave($("#studio-brief-form"))');
 const payload=calls.find(([p])=>p.endsWith('/study-save'))[1];
 assert.equal(payload.context_enabled,false);assert.equal(payload.confirm,false);
 assert.equal(storage.has('yanxu-study-draft:'+id),false);assert.equal(ctx.S.tab,'brief');
 storage.set('yanxu-study-draft:'+id,JSON.stringify({revision:0,fields}));
 run('studioEdit()');assert.match(modalHTML,/检测到其他版本/);assert.ok(!modalHTML.includes('已恢复此版本'));
 ctx.localStorage.setItem=()=>{throw Error('quota')};assert.equal(run('cacheStudyDraft()'),false);
 console.log('Study workspace, escaping, navigation, draft isolation and failed-save recovery passed.');
})().catch(error=>{console.error(error);process.exitCode=1});
