const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const elements={'#update-ack':{checked:false},'#update-badge':{classList:{toggle(){}}},
 '#update-settings-status':{},'#update-status':{classList:{toggle(){}}},'#update-details':{innerHTML:'',dataset:{}},
 '#update-auto':{},'#update-ack-label':{},'#update-actions':{innerHTML:'',dataset:{}}};
let calls=[];
const ctx=vm.createContext({document:{addEventListener(){}},$:s=>elements[s],
 S:{catalog:{version:'0.2.3'},draft:null},e:s=>String(s).replaceAll('<','&lt;').replaceAll('>','&gt;'),
 icon:()=>'',button:(action,label)=>`<button data-action="${action}">${label}</button>`,
 api:async(p,d)=>{calls.push([p,d]);return {}},setTimeout:()=>1,clearTimeout(){},toast(){},modal(){}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/updates.js'),'utf8'),ctx);
const run=code=>vm.runInContext(code,ctx);
(async()=>{
 assert.match(run("updateSummary({phase:'downloading',received:5,release:{size:10}})"),/50%/);
 run("updateState={phase:'ready',current_version:'0.2.3',release:{version:'0.2.4',notes:'<script>bad</script>',page:'https://github.com'},can_update:true,installable:true};paintUpdate()");
 assert.ok(!elements['#update-details'].innerHTML.includes('<script>'));
 assert.ok(elements['#update-details'].innerHTML.includes('&lt;script&gt;'));
 assert.ok(elements['#update-actions'].innerHTML.includes('安装并重启'));
 elements['#update-actions'].innerHTML='DOM serialized controls';run('paintUpdate()');
 assert.equal(elements['#update-actions'].innerHTML,'DOM serialized controls','Polling must preserve control focus');
 await assert.rejects(run("updateAction('update-install')"),/勾选/);
 assert.equal(calls.length,0);
 elements['#update-ack'].checked=true;ctx.S.draft='unsaved';
 await assert.rejects(run("updateAction('update-install')"),/保存正在编辑/);
 assert.equal(calls.length,0);
 ctx.S.draft=null;
 await run("updateAction('update-install')");
 assert.equal(calls[0][0],'updates/install');assert.equal(calls[0][1].acknowledge,true);
 console.log('Update UI: progress, escaped notes, explicit consent and draft protection passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
