/* The creation workspace uses the app's existing controls and authenticated API. */
window.PeakCutCreation = (() => {
  const C = {cid:"", pid:"", project:null, projects:[], memory:null, settings:null,
    projectStamp:"", listStamp:"", theme:"", duration:60, references:"", brief:"", feedback:"", questions:""};
  const base = () => `/api/creation/projects/${encodeURIComponent(C.cid)}/${encodeURIComponent(C.pid)}`;
  const file = (name, download=false) => base() + "/files/" + name.split("/").map(encodeURIComponent).join("/") + (download ? "?download=1" : "");
  const button = (action, text, extra="", primary=false) => `<button type="button" class="btn ${primary ? "primary" : ""}" data-create="${action}" ${extra}>${esc(text)}</button>`;
  const labels = {waiting_niche:"Confirmação do nicho", ready:"Pronto para continuar", queued:"Na fila", running:"Em produção", waiting_references:"Decisão sobre a pesquisa", waiting_feedback:"Revisão do piloto", waiting_clarification:"Esclarecimento necessário", pending:"Aguardando ajuste ou configuração", error:"Execução interrompida", cancelled:"Interrompido", cancelling:"Interrompendo", interrupted:"Pronto para retomar", done:"Concluído"};

  function mount() {
    const panel = document.querySelector("#creation-panel");
    if (!panel) return;
    panel.innerHTML = `<div class="creation-grid"><section class="creation-main">
      <p class="help">Do tema ao vídeo editado, com pesquisa, narração e revisão. Formato vertical 9:16.</p>
      <p class="help">A pesquisa e o roteiro usam a IA já selecionada no PeakCut, por assinatura ou chave API. <a href="/app/settings/ai" data-act="go" data-v="settings" data-tab="ai">Abrir configurações de IA</a>.</p>
      <form id="creation-new" class="creation-form"><label class="f">Canal<select id="creation-channel"><option value="">Escolha um canal</option>${S.creators.map(c=>`<option value="${esc(c.id)}" ${c.id===C.cid?"selected":""}>${esc(c.name)}</option>`).join("")}</select></label>
      <label class="f">Tema do vídeo<input id="creation-theme" data-creation-draft="theme" maxlength="2000" required value="${esc(C.theme)}" placeholder="Qual assunto você quer explicar?"></label>
      <div class="row"><label class="f">Duração desejada (segundos)<input type="number" id="creation-duration" data-creation-draft="duration" min="5" max="3600" value="${esc(C.duration)}"></label><span class="help">A narração define os tempos finais.</span></div>
      <label class="f">Referências opcionais<textarea id="creation-references" data-creation-draft="references" rows="3" placeholder="Um link por linha. A pesquisa também buscará outras fontes.">${esc(C.references)}</textarea></label>
      <label class="f">Orientações para este vídeo<textarea id="creation-brief" data-creation-draft="brief" rows="2" placeholder="Público, abordagem ou uma preferência específica.">${esc(C.brief)}</textarea></label>
      <button class="btn primary" type="submit">Criar vídeo</button></form>
      <div id="creation-project" aria-live="polite"></div>
      </section><aside class="creation-side"><section><h3>Seus projetos</h3><div id="creation-list"></div></section>
      <details><summary>Memória do canal</summary><div id="creation-memory"></div></details>
      <details><summary>Pesquisa, voz e revisão</summary><div id="creation-services"></div></details>
      <p class="help">Cada etapa relê as instruções da pasta criacao_de_videos e consulta os documentos do projeto. As vozes são sintetizadas por IA.</p></aside></div>`;
    C.projectStamp = ""; C.listStamp = "";
    load().catch(error=>toast(error.message,"bad"));
  }

  async function load() {
    const [projects, settings] = await Promise.all([api("/api/creation/projects"),api("/api/creation/settings")]);
    C.projects = projects; C.settings = settings;
    if (!C.cid && S.creators.length===1) { C.cid=S.creators[0].id; const s=document.querySelector("#creation-channel"); if(s)s.value=C.cid; }
    if (C.cid) C.memory = await api("/api/creation/memory/"+encodeURIComponent(C.cid));
    drawMemory(); drawServices(); drawList();
    if (C.pid) { C.project=await api(base()); drawProject(); }
    else document.querySelector("#creation-project").innerHTML = `<p class="help">Escolha um projeto para revisar ou informe um novo tema acima.</p>`;
  }

  function drawList() {
    const target=document.querySelector("#creation-list"); if(!target)return;
    const stamp=JSON.stringify(C.projects); if(stamp===C.listStamp)return; C.listStamp=stamp;
    target.innerHTML=C.projects.length ? `<ul class="creation-projects">${C.projects.map(p=>`<li><button type="button" data-create="select" data-cid="${esc(p.creator)}" data-pid="${esc(p.id)}"><b>${esc(p.theme)}</b><span>${esc(labels[p.status]||p.status)} · etapa ${p.stage}/5</span></button></li>`).join("")}</ul>` : `<p class="help">Seus vídeos em produção aparecerão aqui.</p>`;
  }

  function drawMemory() {
    const target=document.querySelector("#creation-memory"); if(!target)return;
    const m=C.memory;
    if(!C.cid||!m){target.innerHTML='<p class="help">Escolha um canal para consultar suas preferências.</p>';return;}
    target.innerHTML=`<p class="help">Nicho: ${esc(m.niche||"Ainda não definido")}${m.confirmed?" · confirmado":""}</p>
      <label class="f">Nicho para os próximos projetos<input id="creation-memory-niche" value="${esc(m.niche||"")}" maxlength="300"></label>
      ${button("save-niche","Confirmar ou alterar nicho")}
      <label class="f">Preferências (uma por linha)<textarea id="creation-preferences" rows="7">${esc((m.preferences||[]).map(p=>`${p.key}: ${p.value}`).join("\n"))}</textarea></label>
      ${button("save-memory","Salvar preferências")}
      <p class="help">Essas preferências valem para os próximos vídeos deste canal. Ajustes de um projeto só são salvos aqui quando você pedir.</p>
      <h4>Vozes favoritas</h4>${(m.voices||[]).map((v,i)=>'<div class="row">'+button("favorite",v.name||`${v.provider} · ${v.voice}`,`data-index="${i}"`)+button("remove-favorite","Remover",`data-index="${i}" aria-label="Remover voz ${esc(v.name||v.voice)}"`)+'</div>').join(" ")||'<p class="help">Salve uma voz em Pesquisa, voz e revisão.</p>'}`;
  }

  function drawServices() {
    const target=document.querySelector("#creation-services"), s=C.settings; if(!target||!s)return;
    target.innerHTML=`<form id="creation-settings" class="creation-form">
      <label class="f">Chave de pesquisa Google (SerpAPI)<input id="creation-search-key" type="password" autocomplete="new-password" placeholder="${s.search.has_key?"Configurada; vazio mantém a chave":"Configure sua chave"}"></label>
      <label class="f">Serviço de voz<select id="creation-voice-provider"><option value="elevenlabs" ${s.voice.provider==="elevenlabs"?"selected":""}>ElevenLabs</option><option value="openai" ${s.voice.provider==="openai"?"selected":""}>OpenAI</option></select></label>
      <p class="help">Chave de narração: ${s.voice.has_key?"configurada":"ainda não configurada"}. <a href="/app/settings/ai" data-act="go" data-v="settings" data-tab="ai">Configurar em Configurações → IA</a>.</p>
      <label class="f">Modelo de voz<input id="creation-voice-model" value="${esc(s.voice.model||"")}" placeholder="Identificador do modelo"></label>
      <label class="f">Voz<input id="creation-voice-id" list="creation-voices" value="${esc(s.voice.voice||"")}" placeholder="Identificador da voz"><datalist id="creation-voices"></datalist></label>
      <label class="f">Velocidade<input id="creation-voice-speed" type="number" step="0.05" min="0.25" max="4" value="${esc(s.voice.speed||1)}"></label>
      <label class="f">Estilo de interpretação<textarea id="creation-voice-direction" rows="2">${esc(s.voice.direction||"")}</textarea></label>
      <p class="help">OpenAI Speech aceita direção textual nos modelos compatíveis. ElevenLabs usa os controles do modelo; instruções textuais separadas não são aplicadas automaticamente.</p>
      ${s.voice.provider==="openai"?'<p class="help">Os modelos legados da API Speech têm encerramento anunciado para 6 de janeiro de 2027. Use um modelo atualmente compatível com esse endpoint.</p>':""}
      <label class="f">Chave Gemini para ouvir e revisar áudio e vídeo<input id="creation-review-key" type="password" autocomplete="new-password" placeholder="${s.review.has_key?"Configurada; vazio mantém a chave":"Configure sua chave"}"></label>
      <label class="f">Modelo de revisão audiovisual<input id="creation-review-model" value="${esc(s.review.model||"")}" placeholder="Modelo Gemini com áudio e vídeo"></label>
      <details><summary>Contexto do modelo de texto</summary><label class="f">Limite em caracteres<input id="creation-context" type="number" min="20000" max="2000000" value="${s.max_context_chars}"></label><p class="help">Use um limite compatível com o modelo. Documentos não são cortados silenciosamente.</p></details>
      <button type="submit" class="btn primary">Salvar serviços</button>
      <div class="row">${button("voices","Consultar vozes")}${button("sample","Ouvir amostra")}${button("save-favorite","Salvar voz favorita")}</div><div id="creation-sample"></div>
      <p class="help">As chaves ficam no servidor, separadas dos prompts e documentos. Os serviços podem cobrar pelo uso. A IA de texto é escolhida nas configurações gerais do PeakCut.</p></form>`;
  }

  async function mountNarrationSettings(target) {
    if(!target)return;
    try {
      const settings=await api("/api/creation/settings");
      if(!target.isConnected)return;
      C.settings=settings;
      const voice=settings.voice;
      target.innerHTML=`<h2>Narração do vídeo</h2>
        <p class="help">Configure uma chave API específica para sintetizar a voz dos vídeos. A assinatura ou chave escolhida acima continua sendo usada para pesquisa e roteiro.</p>
        <form id="creation-narration-settings" class="ai-form">
          <label class="f">Serviço de narração<select id="narration-api-provider"><option value="elevenlabs" ${voice.provider==="elevenlabs"?"selected":""}>ElevenLabs</option><option value="openai" ${voice.provider==="openai"?"selected":""}>OpenAI</option></select></label>
          <label class="f">Chave API para criar narração<input id="narration-api-key" type="password" autocomplete="new-password" spellcheck="false" placeholder="${voice.has_key?"Chave salva. Deixe vazio para manter.":"Cole a chave do serviço de narração"}"><span class="hint">Essa chave fica no servidor e não é enviada aos prompts. Ela é independente da conexão de IA de texto.</span></label>
          <label class="f">Modelo de narração<input id="narration-api-model" value="${esc(voice.model||"")}" spellcheck="false"></label>
          <label class="f">Voz<input id="narration-api-voice" value="${esc(voice.voice||"")}" spellcheck="false"><span class="hint">Você também pode consultar vozes, ouvir amostras e salvar favoritas na aba Criar vídeo.</span></label>
          <p class="help" role="status">${voice.has_key?"Chave de narração configurada.":"Nenhuma chave de narração configurada."}</p>
          <div class="row"><button type="submit" class="btn primary">Salvar narração</button><a class="btn" href="/app/create" data-act="go" data-v="create">Abrir Criar vídeo</a></div>
        </form>`;
    }catch(error){
      if(target.isConnected)target.innerHTML=`<h2>Narração do vídeo</h2><p class="help" role="alert">${esc(error.message)}</p>`;
    }
  }

  async function saveNarrationSettings() {
    const value=name=>document.querySelector("#narration-api-"+name).value.trim();
    const voice={provider:value("provider"),model:value("model"),voice:value("voice")};
    if(value("key"))voice.key=value("key");
    C.settings=await send("/api/creation/settings",{voice},"PUT");
    const input=document.querySelector("#narration-api-key");
    if(input)input.value="";
  }

  function drawProject() {
    const target=document.querySelector("#creation-project"), p=C.project; if(!target||!p)return;
    const stamp=JSON.stringify(p); if(stamp===C.projectStamp)return; C.projectStamp=stamp;
    const active=["queued","running","cancelling"].includes(p.status);
    const docs=Object.entries(p.documents||{}).map(([name,item])=>{
      const previous=Array.from({length:Math.max(0,item.revision-1)},(_,i)=>i+1).map(r=>`<a href="${file(item.path.replace(/r\d+\.md$/,`r${String(r).padStart(4,"0")}.md`))}" target="_blank" rel="noopener">r${r}</a>`).join(" · ");
      return `<li><a href="${file(item.path)}" target="_blank" rel="noopener">${esc(name)}</a> <span class="help">r${item.revision}</span>${previous?`<details><summary>Versões anteriores</summary>${previous}</details>`:""}</li>`;
    }).join("");
    const video=p.artifacts?.final||p.artifacts?.pilot;
    let actions="";
    if(p.status==="waiting_niche") actions=`<label class="f">Nicho do canal<input id="creation-niche" value="${esc(p.niche_proposal?.niche||C.memory?.niche||"")}" placeholder="Informe ou corrija o nicho"></label><p class="help">${esc(p.niche_proposal?.reason||p.niche_proposal?.evidence?.message||"Confirme o nicho para iniciar a pesquisa.")}</p>${button("confirm-niche","Confirmar nicho e continuar","",true)} ${button("infer-niche","Analisar canal novamente")}`;
    else if(p.status==="waiting_references") actions=`<p>Foram consultadas ${p.source_count||0} referências válidas.</p><div class="row">${button("deepen","Aprofundar pesquisa","",true)}${button("advance","Avançar com estas referências")}</div>`;
    else if(active) actions=button("cancel","Interromper execução");
    else if(p.status!=="done") actions=button("run","Retomar etapa","",true);
    const feedback=video&&!active&&p.status!=="done"?`<section class="creation-feedback"><h3>Revisão</h3>${p.questions?.length?`<ul>${p.questions.map(q=>`<li>${esc(q)}</li>`).join("")}</ul>`:""}<label class="f">Ajustes ou respostas<textarea id="creation-feedback" data-creation-draft="feedback" rows="4" placeholder="Indique o trecho, o que incomoda e como gostaria que ficasse.">${esc(C.feedback)}</textarea></label><label><input type="checkbox" id="creation-remember"> Salvar este pedido também nas preferências do canal</label><div class="row">${button("feedback","Enviar ajustes")}${p.status==="waiting_feedback"?button("approve",`Aprovar piloto r${p.revision} e finalizar`,"",true):""}</div></section>`:"";
    target.innerHTML=`<section class="creation-current"><h2>${esc(p.theme)}</h2><p class="help">${esc(labels[p.status]||p.status)} · etapa ${p.stage}/5 · 9:16</p><p class="creation-message">${esc(p.message||"")}</p>
      ${video?`<video class="creation-preview" controls playsinline preload="metadata" src="${file(video)}"></video><p class="help">${p.artifacts?.final?"Exportação final · 1080×1920":"Piloto completo · 540×960"} · narração sintetizada por IA</p>`:""}
      <div class="creation-controls">${actions}</div>${feedback}
      ${p.artifacts?.final?`<div class="row"><a class="btn primary" href="${file(p.artifacts.final,true)}">Baixar vídeo final</a>${p.artifacts.subtitles?`<a class="btn" href="${file(p.artifacts.subtitles,true)}">Baixar legendas</a>`:""}</div>`:""}
      ${p.artifacts?.timeline?`<p><a href="${file(p.artifacts.timeline,true)}">Baixar timeline</a> · <a href="${base()}/bundle">Baixar projeto e materiais</a></p>`:""}
      <details ${docs?"open":""}><summary>Documentos e referências</summary><ul class="creation-documents">${docs||"<li>Os documentos aparecerão conforme as etapas forem concluídas.</li>"}</ul></details>
      ${!active?`<details><summary>Adicionar mídia ou refazer uma etapa</summary><label class="f">Imagem, vídeo, trilha ou efeito<input type="file" id="creation-asset" accept="image/*,video/*,audio/*"></label>${button("upload","Adicionar mídia ao projeto")}<div class="row"><label class="f">Etapa<select id="creation-restart"><option value="1">1 · Pesquisa</option><option value="2">2 · Roteiro</option><option value="3">3 · Narração</option><option value="4" selected>4 · Montagem</option></select></label>${button("restart","Refazer a partir desta etapa")}</div><p class="help">As versões anteriores são preservadas. Refazer invalida a aprovação da versão atual.</p></details>`:""}</section>`;
  }

  async function saveServices() {
    const value=id=>document.querySelector(id)?.value||"";
    const body={search:{},voice:{provider:value("#creation-voice-provider"),model:value("#creation-voice-model"),voice:value("#creation-voice-id"),speed:Number(value("#creation-voice-speed")),direction:value("#creation-voice-direction")},review:{model:value("#creation-review-model")},max_context_chars:Number(value("#creation-context"))};
    for(const [section,id] of [["search","#creation-search-key"],["review","#creation-review-key"]]) if(value(id))body[section].key=value(id);
    C.settings=await send("/api/creation/settings",body,"PUT");
  }

  async function decision(action, extra={}) {
    await send(base()+"/decision",{action,...extra});
    C.project=await api(base()); C.projects=await api("/api/creation/projects");
    drawProject(); drawList();
  }

  async function act(action, element) {
    if(action==="select") {C.cid=element.dataset.cid;C.pid=element.dataset.pid;C.projectStamp="";document.querySelector("#creation-channel").value=C.cid;return load();}
    if(action==="confirm-niche") {await decision("confirm_niche",{niche:document.querySelector("#creation-niche").value});C.memory=await api("/api/creation/memory/"+C.cid);return drawMemory();}
    if(action==="infer-niche")return decision("niche");
    if(action==="run"||action==="cancel")return decision(action);
    if(action==="advance")return decision("advance_references");
    if(action==="deepen")return decision("deepen_research");
    if(action==="approve")return decision("approve",{revision:C.project.revision});
    if(action==="feedback") {await decision("feedback",{text:C.feedback,remember:document.querySelector("#creation-remember").checked});C.feedback="";return;}
    if(action==="restart")return decision("restart",{stage:Number(document.querySelector("#creation-restart").value)});
    if(action==="save-niche") {const niche=document.querySelector("#creation-memory-niche").value.trim();if(!niche)throw Error("Informe o nicho do canal.");C.memory=await send("/api/creation/memory/"+C.cid,{niche,confirmed:true},"PUT");drawMemory();return toast("Nicho salvo para os próximos projetos.","ok");}
    if(action==="save-memory") {
      const preferences=document.querySelector("#creation-preferences").value.split("\n").filter(v=>v.trim()).map(v=>{const colon=v.indexOf(":");return {key:colon>0?v.slice(0,colon).trim():"preferência",value:colon>0?v.slice(colon+1).trim():v.trim(),status:"declared",source:"user"};});
      C.memory=await send("/api/creation/memory/"+C.cid,{preferences},"PUT");drawMemory();return toast("Preferências salvas.","ok");
    }
    if(action==="voices") {await saveServices();const voices=await api("/api/creation/voices");document.querySelector("#creation-voices").innerHTML=voices.map(v=>`<option value="${esc(v.id)}">${esc(v.name)}</option>`).join("");return toast("Vozes disponíveis no campo Voz.","ok");}
    if(action==="sample") {await saveServices();const result=await send("/api/creation/voice-sample",{});document.querySelector("#creation-sample").innerHTML=`<audio controls src="${esc(result.url)}"></audio>`;return;}
    if(action==="save-favorite") {if(!C.cid)throw Error("Escolha um canal primeiro.");await saveServices();const v=C.settings.voice;if(!v.voice||!v.model)throw Error("Escolha uma voz e um modelo.");C.memory=await send("/api/creation/memory/"+C.cid,{voices:[...(C.memory?.voices||[]),{provider:v.provider,voice:v.voice,model:v.model,speed:v.speed,direction:v.direction||"",name:`${v.provider} · ${v.voice}`}].slice(-100)},"PUT");drawMemory();return toast("Voz favorita salva.","ok");}
    if(action==="favorite") {const v=C.memory.voices[Number(element.dataset.index)];C.settings=await send("/api/creation/settings",{voice:{provider:v.provider,voice:v.voice,model:v.model,speed:Number(v.speed||1),direction:v.direction||""}},"PUT");drawServices();return toast("Voz selecionada. Confira a chave do provedor.","ok");}
    if(action==="remove-favorite") {const voices=C.memory.voices.filter((_,index)=>index!==Number(element.dataset.index));C.memory=await send("/api/creation/memory/"+C.cid,{voices},"PUT");drawMemory();return toast("Voz removida dos favoritos.","ok");}
    if(action==="upload") {const input=document.querySelector("#creation-asset");if(!input.files.length)throw Error("Escolha um arquivo.");const data=new FormData();data.append("file",input.files[0]);await api(base()+"/assets",{method:"POST",body:data});return toast("Mídia adicionada. Retome ou refaça a montagem para utilizá-la.","ok");}
  }

  document.addEventListener("click",async event=>{const element=event.target.closest("[data-create]");if(!element)return;event.preventDefault();element.disabled=true;try{await act(element.dataset.create,element);}catch(error){toast(error.message,"bad");}finally{element.disabled=false;}});
  document.addEventListener("input",event=>{const key=event.target.dataset.creationDraft;if(key)C[key]=event.target.value;});
  document.addEventListener("change",async event=>{if(event.target.id!=="creation-channel")return;C.cid=event.target.value;C.pid="";C.project=null;C.projectStamp="";C.memory=null;try{if(C.cid)C.memory=await api("/api/creation/memory/"+C.cid);drawMemory();document.querySelector("#creation-project").innerHTML="";}catch(error){toast(error.message,"bad");}});
  document.addEventListener("submit",async event=>{
    if(!["creation-new","creation-settings","creation-narration-settings"].includes(event.target.id))return;
    event.preventDefault();const button=event.target.querySelector('button[type="submit"]');button.disabled=true;
    try {
      if(event.target.id==="creation-narration-settings"){await saveNarrationSettings();await mountNarrationSettings(document.querySelector("#narration-settings"));toast("Configuração de narração salva.","ok");}
      else if(event.target.id==="creation-settings"){await saveServices();drawServices();toast("Serviços salvos.","ok");}
      else {if(!C.cid)throw Error("Escolha um canal primeiro.");const p=await send("/api/creation/projects",{creator:C.cid,theme:C.theme,duration:Number(C.duration),references:C.references,brief:C.brief});C.pid=p.id;await load();}
    }catch(error){toast(error.message,"bad");}finally{button.disabled=false;}
  });

  function refresh(projects) {
    if(!document.querySelector("#creation-panel"))return;
    if(Array.isArray(projects)){C.projects=projects;drawList();const current=projects.find(p=>p.id===C.pid&&p.creator===C.cid);if(current){C.project=current;drawProject();}}
  }
  return {mount,refresh,mountNarrationSettings};
})();
