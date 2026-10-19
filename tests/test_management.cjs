const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const id=n=>String(n).padStart(32,'0');
const bar={innerHTML:''};let lastModal='',apiCalls=[],closed=0;
const body={classList:{remove(){}},append(){}};
const ctx=vm.createContext({document:{querySelector:s=>s==='#project-batch-bar'?bar:null,querySelectorAll:()=>[],addEventListener(){},body,createElement:()=>({dataset:{},remove(){}})},window:{addEventListener(){}},
 S:{projects:[],catalog:{flows:{full:{name:'完整流程',color:'green',route:'ars'}},stages:{research:{title:'研究探索'}}},view:'projects',filter:'all',query:'',draft:null},
 e:s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
 icon:()=>'',button:(a,t,k='',i='',attrs='')=>`<button data-action="${a}" ${attrs}>${t}</button>`,
 projectRoute:p=>p.route,flow:()=>({color:'green',name:'完整流程'}),progress:()=>0,step:()=> 'research',routeName:()=> '开题与写作',date:()=> 'today',
 modal:html=>lastModal=html,closeModal:()=>closed++,toast(){},render(){},
 api:async(p,d)=>{apiCalls.push([p,d]);return p==='preferences/onboarding'?d:{count:1,warnings:[]}}});
vm.runInContext("const $=s=>document.querySelector(s);",ctx);
for(const file of ['projects.js','onboarding.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../web',file),'utf8'),ctx);
const run=s=>vm.runInContext(s,ctx);
ctx.S.projects=[{id:id(1),title:'Alpha <img>',topic:'gene',flow:'full',route:'ars',updated:'2026-10-01',created:'2026-10-01',asset_count:2},
 {id:id(2),title:'Beta',topic:'other',flow:'full',route:'ars',pinned:true,updated:'2026-09-01',created:'2026-09-01',asset_count:0},
 {id:id(3),title:'Archived',topic:'gene',flow:'full',route:'ars',lifecycle:'archived'},
 {id:id(4),title:'Deleted',topic:'gene',flow:'full',route:'ars',lifecycle:'trash'},
 {id:id(5),title:'Running',topic:'gene',flow:'full',route:'ars',updated:'2026-08-01',running:true,asset_count:0}];
(async()=>{
 assert.equal(run('activeProjects().length'),3);assert.equal(run('visibleProjects()[0].title'),'Beta');
 run(`PM.selected=new Set(['${id(1)}','${id(2)}','${id(5)}']);S.query='gene';paintProjectSelection()`);
 assert.equal(run('PM.selected.size'),1);assert.equal(run('PM.selected.has("'+id(1)+'")'),true);
 assert.match(run('managedProjectCard(S.projects[0])'),/Alpha &lt;img&gt;/);
 assert.ok(!run('managedProjectCard(S.projects[0])').includes('<img>'));
 run(`managementModal('purge',['${id(4)}'])`);assert.match(lastModal,/请输入“永久删除”确认/);assert.match(lastModal,/无法在软件内撤销/);
 run(`managementModal('duplicate',['${id(1)}'])`);assert.match(lastModal,/待核实/);assert.match(lastModal,/name="mode" value="materials" checked/);
 const apiOriginal=ctx.api;ctx.api=async()=>{throw Error('保存失败')};
 await assert.rejects(run(`applyProjectManagement('archive',['${id(1)}'])`),/保存失败/);assert.equal(run('PM.busy'),false);ctx.api=apiOriginal;
 run('guideShown=[];showGuide=async step=>guideShown.push(step);Guide.active=false;');
 ctx.S.projects=[];await run("initOnboarding({status:'not_started',step:0})");assert.equal(run('guideShown.at(-1)'),0);
 run('guideShown=[]');ctx.S.projects=[{id:id(1)}];await run("initOnboarding({status:'not_started',step:0})");assert.equal(run('guideShown.length'),0,'Existing workspaces must not be interrupted');
 await run("initOnboarding({status:'in_progress',step:3})");assert.equal(run('guideShown.at(-1)'),3);
 run('Guide.active=true');await run("guideAction('guide-skip',{})");assert.equal(run('Guide.state.status'),'dismissed');assert.equal(run('Guide.active'),false);
 run('Guide.active=true');await run("guideAction('guide-finish',{})");assert.equal(run('Guide.state.status'),'completed');
 ctx.S.draft='unsaved';await assert.rejects(run("guideAction('guide-start',{})"),/先保存/);ctx.S.draft=null;
 await run("guideAction('guide-start',{})");assert.equal(run('guideShown.at(-1)'),0);
 assert.ok(apiCalls.every(([p])=>!p.includes('connection')&&!p.includes('/run')),'Guide must never call a model or test connection');
 console.log('Project management UI: filtered selection, running guards, safe names, consent and first-use/resumable guide passed.');
})().catch(error=>{console.error(error);process.exitCode=1});
