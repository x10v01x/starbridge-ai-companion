// Execute the real bundle against the real HTML IDs. Missing DOM bindings must fail.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
const source = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');

function element() {
  return {hidden:false,textContent:'',value:'',className:'',checked:true,disabled:false,events:{},
    classList:{toggle(){}},addEventListener(name,fn){this.events[name]=fn;},
    append(){},replaceChildren(){},focus(){},close(){this.open=false;},showModal(){this.open=true;},
    querySelector(){return element();}};
}
async function scenario(signedIn, search='') {
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>['#'+m[1],element()]));
  const calls=[];
  const context={console,Intl,Date,Number,Error,URLSearchParams,AbortSignal,encodeURIComponent,
    setTimeout(){return 1;},clearTimeout(){},setInterval(){},location:{search,hash:''},
    sessionStorage:{getItem(){return null;},setItem(){}},
    document:{hidden:false,title:'',querySelector:s=>nodes.get(s)||null,querySelectorAll:()=>[],
      createElement:()=>element(),createTextNode:text=>({textContent:text}),addEventListener(){}},
    fetch:async url=>{
      calls.push(url);
      if(url==='/api/public')return {ok:true,json:async()=>({brand:'Test',quota_per_point:5000})};
      if(url==='/api/me')return {ok:signedIn,status:signedIn?200:401,json:async()=>signedIn?
        {id:1,username:'test',display_name:'Test',points:1.9876,used_points:0.1234,balance:19876,requests:0,updated_at:1}:{detail:'登录已过期'}};
      if(url.startsWith('/api/desktop/request?'))return {ok:true,json:async()=>({code:'ABC123'})};
      throw new Error('Unexpected request: '+url);
    }};
  vm.runInNewContext(source,context,{filename:'app.js'});
  await new Promise(resolve=>setImmediate(resolve));
  assert(calls.includes('/api/public'),'startup must reach /api/public');
  assert(calls.includes('/api/me'),'new page must restore its session');
  assert(nodes.get('#toggle-register').events.click,'registration toggle must exist and bind');
  if(signedIn){assert.equal(nodes.get('#workspace').hidden,false,'existing session must show dashboard');assert.equal(nodes.get('#balance').textContent,'1');assert.equal(nodes.get('#used').textContent,'0');assert.match(nodes.get('#balance').title,/1\.9876/);}
  if(search&&signedIn){assert.equal(nodes.get('#desktop-dialog').open,true);assert.equal(nodes.get('#desktop-code').textContent,'ABC123');}
  if(search&&!signedIn)assert.equal(nodes.get('#login-dialog').open,true,'desktop link must open login automatically');
}
(async()=>{await scenario(false);await scenario(true);await scenario(false,'?desktop=request-id');await scenario(true,'?desktop=request-id');console.log('PASS: anonymous boot, new-page session restore, desktop login, existing-session authorization');})().catch(e=>{console.error(e);process.exitCode=1;});
