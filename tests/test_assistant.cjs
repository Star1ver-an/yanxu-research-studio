const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const events={},els={'meta[name="research-token"]':{content:'test'}};
const ctx=vm.createContext({document:{querySelector:s=>els[s],getElementById:()=>null,addEventListener:(type,fn)=>(events[type]||=[]).push(fn),body:{classList:{toggle:()=>{}}}},window:{history:{pushState:()=>{}}},crypto:require('node:crypto').webcrypto,setTimeout:()=>1,clearTimeout:()=>{},URL,Date,Intl,Set,console});
for(const file of ['assistant.js','app.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../web',file),'utf8').replace(/\binit\(\);\s*$/,''),ctx);
const run=code=>vm.runInContext(code,ctx);
(async()=>{
 run(`S.project={id:'a'.repeat(32),title:'RAG',runs:[]};S.view='project';S.tab='materials';S.activeStage='research';AS.scope=S.project.id;`);
 assert.equal(run('assistantScope()'),'a'.repeat(32));assert.equal(run('assistantLocation()'),'materials');
 run(`S.view='home'`);assert.equal(run('assistantScope()'),'');run(`S.view='project'`);
 assert.equal(run('assistantOptions().report'),false);assert.equal(run('assistantOptions().materials.length'),0);
 run(`assistantRememberError('A 项目的错误');AS.scope='b'.repeat(32)`);assert.equal(run('assistantLastError()'),'');run(`AS.scope='a'.repeat(32)`);assert.equal(run('assistantLastError()'),'A 项目的错误');
 run(`AS.drafts[AS.scope]='问题 A';AS.scope='b'.repeat(32);AS.drafts[AS.scope]='问题 B'`);
 assert.equal(run(`AS.drafts['a'.repeat(32)]`),'问题 A');
 const html=run(`assistantMessage({role:'assistant',status:'completed',mode:'api',text:'<img src=x onerror=alert(1)>',actions:[{id:'delete_all',label:'恶意操作'},{id:'materials',label:'<script>材料</script>'}],sources:['<b>来源</b>'],outdated:true})`);
 assert.ok(!html.includes('<img'));assert.ok(!html.includes('<script>'));assert.ok(!html.includes('data-target="delete_all"'));assert.ok(html.includes('data-target="materials"'));assert.ok(html.includes('内容或软件版本已更新'));
 run(`calls=[];api=async(path,data)=>{calls.push({path,data});return {sources:['软件指南'],context_chars:12,sha256:'hash'}};AS.scope='a'.repeat(32);AS.state={job:null};AS.drafts[AS.scope]='保留问题';`);
 await run('assistantPreview()');assert.equal(run('calls.length'),1);assert.equal(run('calls[0].path'),'assistant/preview');
 assert.equal(run('AS.preview.text'),'保留问题');assert.equal(run('AS.drafts[AS.scope]'),'保留问题');
 run(`api=async(path,data)=>{calls.push({path,data});throw Error('连接失败')}`);
 await run('assistantSend()');assert.equal(run('AS.drafts[AS.scope]'),'保留问题');assert.equal(run('AS.error'),'连接失败');
 run(`api=async(path,data)=>{calls.push({path,data});return {messages:[],job:null}}`);
 await run('assistantSend()');assert.equal(run('AS.drafts[AS.scope]'),'');
 assert.equal(run('calls.at(-1).data.context_sha256'),'hash');assert.match(run('calls.at(-1).data.request_id'),/^[a-f0-9]{32}$/);
 const count=run('calls.length');await run('assistantJump("delete_all")');assert.equal(run('calls.length'),count);
 // A delayed answer from another project must not overwrite the visible scope.
 run(`AS.drafts[AS.scope]='另一个问题';AS.preview={text:'另一个问题',data:assistantData(),sha256:'h'};api=()=>new Promise(resolve=>{resolveSend=resolve});pending=assistantSend();AS.scope='b'.repeat(32);AS.state={title:'B',job:null};resolveSend({title:'A',messages:[],job:null})`);
 await run('pending');assert.equal(run('AS.state.title'),'B');
 // Both DOM and state can change between preview and submitting the question.
 run(`AS.scope='a'.repeat(32);AS.drafts[AS.scope]='新问题';AS.preview={text:'旧问题'};calls=[];api=async()=>{calls.push(1)}`);
 await run('assistantSend()');assert.equal(run('calls.length'),0);
 const css=fs.readFileSync(path.join(__dirname,'../web/assistant.css'),'utf8');assert.ok(css.includes('.assistant-open .app-body{display:none}'));assert.ok(css.includes('min-width:1600px'));assert.ok(css.includes('overscroll-behavior:contain'));
 console.log('Assistant frontend checks passed: scope isolation, opt-in context, escaped replies, navigation whitelist, preview, failure recovery and delayed response isolation.');
})().catch(e=>{console.error(e);process.exitCode=1});
