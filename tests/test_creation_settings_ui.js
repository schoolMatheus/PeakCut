/* Narration credentials share the existing AI settings page and private API. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const script = fs.readFileSync("web/creation.js", "utf8");
const html = fs.readFileSync("web/index.html", "utf8");
const saved = {search:{has_key:true},voice:{provider:"elevenlabs",model:"eleven_multilingual_v2",voice:"voice-one",speed:1,has_key:true},review:{has_key:true,model:"review-model"},max_context_chars:240000};
const listeners = {}, requests = [], nodes = {};
const target = {isConnected:true,innerHTML:""};
nodes["#narration-settings"] = target;
const escape = value => String(value).replaceAll("&","&amp;").replaceAll('"',"&quot;").replaceAll("<","&lt;");
const context = vm.createContext({window:{},document:{querySelector:s=>nodes[s]||null,addEventListener:(name,fn)=>{listeners[name]=fn;}},
  S:{status:{ai:{provider:"codex",ready:true},user:{is_admin:true}},creators:[]},esc:escape,
  api:async url=>{assert.equal(url,"/api/creation/settings");return structuredClone(saved);},
  send:async(url,body,method)=>{requests.push({url,body,method});return structuredClone(saved);},toast:()=>{}});
vm.runInContext(script,context);
(async()=>{
  await context.window.PeakCutCreation.mountNarrationSettings(target);
  assert.match(target.innerHTML,/id="narration-api-key"/);
  assert.match(target.innerHTML,/type="password"/);
  assert.match(target.innerHTML,/Narração do vídeo/);
  for(const [name,value] of Object.entries({provider:"elevenlabs",key:"voice-only-test-key",model:"eleven_multilingual_v2",voice:"voice-one"}))
    nodes["#narration-api-"+name]={value};
  const submit={disabled:false};
  const form={id:"creation-narration-settings",querySelector:()=>submit};
  await listeners.submit({target:form,preventDefault(){}});
  assert.equal(requests.length,1);
  assert.equal(requests[0].url,"/api/creation/settings");
  assert.equal(requests[0].method,"PUT");
  assert.equal(requests[0].body.voice.key,"voice-only-test-key");
  assert.deepEqual(Object.keys(requests[0].body),["voice"]);
  requests.length=0;nodes["#narration-api-key"].value="";
  await listeners.submit({target:form,preventDefault(){}});
  assert.equal("key" in requests[0].body.voice,false,"Empty field must preserve the saved key");
  assert.equal(context.S.status.ai.provider,"codex","Saving voice must preserve the chosen subscription");
  const services=script.slice(script.indexOf("function drawServices()"),script.indexOf("function drawProject()"));
  assert.doesNotMatch(services,/id="creation-voice-key"/);
  assert.match(html,/mountNarrationSettings/);
  console.log("PASS narration settings, independent key save, blank-key preservation and existing subscription");
})().catch(error=>{console.error(error);process.exitCode=1;});
