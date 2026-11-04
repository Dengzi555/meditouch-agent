const $=s=>document.querySelector(s);const escapeHTML=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const contextLabels={population:'年龄段 / 人群',renal_function:'肾功能情况',symptoms:'主要症状',duration:'持续时间及变化',temperature:'体温及测量时间',temperature_method:'体温测量方式及部位',associated_symptoms:'伴随症状',risk_factors:'与本次问题相关的健康背景',drug_name:'咨询药品的完整名称及规格',drug_ingredients:'说明书标注的有效成分',medications:'已用药、剂量、服用时间及已知过敏',label_sodium:'标签钠含量及单位',label_basis:'标签每份基准',consumed_amount:'实际食用量'};
let activeSession=null,sessionRevision=0;
let activeQuestion='';
let missingFields=[];
let clarificationQuestions={};
const sessionKey='zhiheng.consultation.v1';
const historyKey='zhiheng.chat-history.v1';

function readHistory(){
  try{
    const entries=JSON.parse(localStorage.getItem(historyKey)??'[]');
    return Array.isArray(entries)?entries.filter(entry=>
      entry&&/^[a-f0-9]{32}$/.test(entry.id)&&typeof entry.title==='string'):[];
  }catch{return [];}
}

let chatHistory=readHistory();

function renderHistory(){
  $('#chat-history').replaceChildren(...chatHistory.map(entry=>{
    const item=document.createElement('li');
    const button=document.createElement('button');
    button.type='button';
    button.dataset.session=entry.id;
    button.textContent=entry.title;
    button.title=entry.title;
    if(entry.id===activeSession)button.setAttribute('aria-current','true');
    item.append(button);
    return item;
  }));
}

function rememberConversation(){
  const entries=new Map([...readHistory(),...chatHistory].map(entry=>[entry.id,entry]));
  chatHistory=entries.has(activeSession)?[...entries.values()]:
    [{id:activeSession,title:activeQuestion.replace(/\s+/g,' ').slice(0,100)},...entries.values()];
  try{localStorage.setItem(historyKey,JSON.stringify(chatHistory));}catch{}
  renderHistory();
}
const followupOptions={
  fever_intent:[['吃什么食物、怎么补水','饮食'],['退烧用药','用药']],
  symptoms:['发热','咳嗽','喉咙痛','鼻塞或流涕','肌肉酸痛','乏力'],
  associated_symptoms:['发热','咳嗽','喉咙痛','鼻塞或流涕','肌肉酸痛','乏力'],
  duration:['不到一天','1–3 天','4–7 天','超过一周','不清楚'],
  population:['成人','儿童','老人','孕产妇','不清楚'],
  temperature:['未测量','不清楚'],
  temperature_method:['腋下','口腔','耳温','额温','不清楚'],
};

function showClarification(){
  const form=$('#clarification-form');
  form.classList.toggle('hidden',!missingFields.length);
  $('#clarification-fields').replaceChildren(...missingFields.map(key=>{
    const group=document.createElement('fieldset');
    const legend=document.createElement('legend');
    const multi=['symptoms','associated_symptoms'].includes(key);
    legend.textContent=(key==='fever_intent'?clarificationQuestions[key]:
      contextLabels[key]??clarificationQuestions[key]??key)+(multi?'（可多选）':'');
    group.append(legend);
    const options=followupOptions[key]??[];
    if(options.length){
      const choices=document.createElement('div');
      choices.className='followup-options';
      for(const option of options){
        const [title,value]=Array.isArray(option)?option:[option,option];
        const label=document.createElement('label');
        const choice=document.createElement('input');
        choice.type=multi?'checkbox':'radio';
        choice.name=key;
        choice.value=value;
        label.append(choice,Object.assign(document.createElement('span'),{textContent:title}));
        choices.append(label);
      }
      group.append(choices);
    }
    if(key!=='fever_intent'){
      const input=document.createElement('input');
      input.name=key+'_text';
      input.maxLength=300;
      input.placeholder=options.length?(multi?'补充其他症状':'也可直接填写'):'不确定可填写“不清楚”';
      input.setAttribute('aria-label',clarificationQuestions[key]??legend.textContent);
      if(!options.length&&clarificationQuestions[key])legend.textContent=clarificationQuestions[key];
      group.append(input);
    }
    return group;
  }));
}
function applySession(r){
  activeSession=r.session_id;sessionRevision=r.revision;sessionStorage.setItem(sessionKey,activeSession);
  activeQuestion=r.original_question??r.question;
  showConversationTitle(activeQuestion);
  rememberConversation();
  missingFields=r.status==='needs_clarification'?(r.missing_context??[]):[];
  clarificationQuestions=r.clarification_questions??r.questions??{};
  showClarification();
}
function newConsultation(){
  activeSession=null;sessionRevision=0;sessionStorage.removeItem(sessionKey);
  activeQuestion='';
  $('#question').value='';
  missingFields=[];clarificationQuestions={};
  showClarification();closeReferences(false);
  $('#result').innerHTML='';showConversationTitle('');
  $('#history-feedback').textContent='';
  renderHistory();
  $('#submit').textContent='发送 ↑';$('#question').focus();
}
function showConversationTitle(question){
  $('#session-status').textContent=question||'新咨询';
}
async function api(path,data){const r=await fetch('/api/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const v=await r.json();if(!r.ok)throw Error(v.error||'请求失败');return v;}
function sourceLink(d, label){
  try{
    const url=new URL(d.source?.url??d.source_url);
    if(!['https:','http:'].includes(url.protocol))return '';
    const name=label??d.publisher??d.source?.publisher??url.hostname;
    return `<a class="source-chip" href="${escapeHTML(url.href)}" target="_blank" rel="noopener noreferrer"><span aria-hidden="true">↗</span>${escapeHTML(name||url.hostname)}</a>`;
  }catch{return '';}
}
function sourceHTML(d){return `<div class="source"><h3>${escapeHTML(d.title)} ${d.demo?'<span class="badge warn">虚构演示</span>':''}</h3><small>${escapeHTML(d.publisher)} · ${escapeHTML(d.date)} · ${escapeHTML(d.id)}${d.pdf_pages?.length?' · PDF页 '+escapeHTML(d.pdf_pages.join(', ')):d.source_kind==='html'?' · 网页正文':''}</small><p>${sourceLink(d)}</p><p>${escapeHTML(d.raw_quote??d.text)}</p><details><summary>结构化主张与版本关系</summary><pre>${escapeHTML(JSON.stringify({claims:d.claims||[],supersedes:d.supersedes||[]},null,2))}</pre></details></div>`;}
let currentSources=[];
let referenceTrigger=null;

function closeReferences(restoreFocus=true){
  $('#references').classList.add('hidden');
  referenceTrigger?.setAttribute('aria-expanded','false');
  if(restoreFocus)referenceTrigger?.focus();
  referenceTrigger=null;
}

function openReferences(trigger){
  if(referenceTrigger===trigger){closeReferences();return;}
  closeReferences(false);
  referenceTrigger=trigger;
  trigger.setAttribute('aria-expanded','true');
  $('#reference-title').textContent=`${trigger.dataset.references==='related'?'检索到的资料':'参考资料'}（${currentSources.length}）`;
  $('#reference-list').innerHTML=currentSources.map((source,index)=>{
    const metadata=[source.publisher??source.source?.publisher,source.date,
      source.pdf_pages?.length?'PDF 第 '+source.pdf_pages.join('、')+' 页':''].filter(Boolean).join(' · ');
    return `<article class="reference-entry"><h3>${index+1}. ${escapeHTML(source.title)}${source.demo?'（虚构演示）':''}</h3>
      <p>${escapeHTML(metadata)}</p>${sourceLink(source,'打开原文')}
      <details><summary>查看原文摘录</summary><p class="reference-quote">${escapeHTML(source.raw_quote??source.text??'暂无摘录')}</p></details></article>`;
  }).join('');
  $('#references').classList.remove('hidden');
  $('#reference-list').scrollTop=0;
  $('#close-references').focus();
}

$('#close-references').onclick=()=>closeReferences();
$('#result').addEventListener('click',event=>{
  const trigger=event.target.closest('[data-references]');
  if(trigger)openReferences(trigger);
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&!$('#references').classList.contains('hidden'))closeReferences();
});
function evidenceExcerpts(question, sources){
  const dietIntent=/(发烧|发热|感冒|流感|呼吸道感染)/.test(question)&&
    /(饮食|补水|喝水|饮水|食物|吃饭)/.test(question)&&!/(退烧药|用药|吃什么药|布洛芬|对乙酰氨基酚)/.test(question);
  const dietContent=/推荐的食物|少食多餐|补充水分|水分摄入|饮食|饮水|喝水/;
  const terms = new Set(Array.from((question ?? '').matchAll(/(?=([\p{Script=Han}]{2}))/gu),
    match=>match[1]));
  return sources.map(source=>{
    if(dietIntent&&source.audience==='child'&&
       !/儿童|孩子|宝宝|婴幼儿|小儿|小孩|(?<!\d)(?:1[0-7]|[0-9])\s*岁|\d+\s*个月/.test(question))return null;
    const raw=source.raw_quote??source.text??'';
    const lines=raw.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
    const faqHeading=/^\d+[.．、]\s*.+[？?]$/;
    let blocks=lines;
    if(lines.filter(line=>faqHeading.test(line)).length>1){
      blocks=[];
      for(const line of lines){
        if(faqHeading.test(line)||!blocks.length)blocks.push(line);
        else blocks[blocks.length-1]+='\n'+line;
      }
    }
    const candidates=blocks.filter(block=>block.length>=40&&block.length<=700&&/[。！？]$/.test(block)&&
      (!dietIntent||dietContent.test(block)));
    const ranked=candidates.map(excerpt=>({
      excerpt, score:[...terms].filter(term=>excerpt.includes(term)).length+
        (dietIntent&&dietContent.test(source.title??'')?2:0),
    })).sort((a,b)=>b.score-a.score);
    return {source,...ranked[0]};
  }).filter(item=>item?.score>=2).sort((a,b)=>b.score-a.score).slice(0,2);
}
function resultHTML(r, showTrace=false){
  if(!showTrace&&r.status==='needs_clarification')return '';
  const labels={answered:'证据关联校验通过',risk_alert:'风险提醒 · 优先处理',needs_clarification:'需要补充条件',pending_review:'资料摘录',tool_error:'工具调用失败',model_error:'模型调用未完成',budget_exceeded:'已达到执行上限',validation_failed:'回答已被校验拦截',unresolved:'存在未解决分歧',evidence_found:'已找到结构化证据',insufficient:'证据不足'};
  const uncertainty=(r.uncertainty??[]).filter(x=>r.status!=='pending_review'||!x.startsWith('用户声明尚未独立核实')).map(x=>`<p>${escapeHTML(x)}</p>`).join('');
  const riskRefs=(r.risk_references??[]).map(x=>`<p>${escapeHTML(x.title)} ${sourceLink({source:{url:x.url}})}</p>`).join('');
  const sources=r.sources??[];
  const verifiedQuotes=r.status==='answered'?(r.citations??[]).filter(c=>typeof c?.quote==='string'):[];
  const statements=r.status==='answered'&&!sources.some(source=>source.demo)?r.statements??[]:[];
  const preview=r.status==='pending_review';
  const excerpts=preview?evidenceExcerpts(r.question??r.original_question,sources):[];
  const answer=preview ? `<div class="answer">${excerpts.length?excerpts.map(({source,excerpt})=>`<p>${escapeHTML(excerpt)} ${sourceLink(source)}</p>`).join(''):'这次检索找到了资料，但目前没有可用于生成回答的依据。可点上方查看检索结果。'}</div>` : statements.length ? statements.map(statement=>{
    const source=sources.find(item=>item.id===statement.evidence_id);
    return `<div class="answer-section"><p class="answer-point">${escapeHTML(statement.text)} ${source?sourceLink(source):''}</p><p>适用范围：${escapeHTML(statement.applicability)}</p><p>限制：${escapeHTML(statement.limitations)}</p></div>`;
  }).join('') : verifiedQuotes.length ? `<div class="answer">${sources.some(source=>source.demo)?'<p>虚构演示</p>':''}${verifiedQuotes.map(c=>{
    const source=sources.find(item=>item.id===c.evidence_id);
    return `<p>${escapeHTML(c.quote)} ${source?sourceLink(source):''}</p>`;
  }).join('')}</div>` : `<div class="answer">${escapeHTML(r.answer)}</div>`;
  const badge=r.status==='answered'?'':`<span class="badge warn">${preview&&!excerpts.length?'暂无法回答':labels[r.status]||escapeHTML(r.status)}</span>`;
  const references=sources.length?(showTrace
    ?`<details><summary>查看来源 · ${sources.length} 篇</summary>${sources.map(sourceHTML).join('')}</details>`
    :`<button type="button" class="reference-toggle" data-references="${preview?'related':'reference'}" aria-expanded="false" aria-controls="references">${preview?'查看检索结果':'查看参考资料'} · ${sources.length} 条 <span aria-hidden="true">›</span></button>`):'';
  const trace=showTrace?`<details><summary>执行轨迹 · ${escapeHTML(r.engine)} · ${r.latency_ms} ms</summary><pre>${escapeHTML(JSON.stringify(r.trace,null,2))}</pre></details>`:'';
  return `<article class="card chat-answer ${r.status==='risk_alert'?'urgent-answer':''}">${showTrace?'':references}${badge}${answer}${uncertainty}${riskRefs}${r.error?`<p class="error">${escapeHTML(r.error)}</p>`:''}${showTrace?references:''}${trace}</article>`;
}
async function load(){const state=await api('state');$('#engine').textContent=state.config.enabled?'模型已配置 · '+state.config.model:'规则演示模式 · 未配置模型';$('#documents').innerHTML=state.documents.map(sourceHTML).join('');}
$('#chat-form').onsubmit=e=>{
  e.preventDefault();
  const question=$('#question').value.trim();
  if(question){
    if(activeSession)newConsultation();
    submitQuestion({},question);
  }else if(missingFields.length)$('#clarification-form').querySelector('input')?.focus();
  else if(activeQuestion)submitQuestion();
};
$('#question').addEventListener('keydown',e=>{
  if(e.key!=='Enter'||e.shiftKey||e.isComposing||e.keyCode===229)return;
  e.preventDefault();
  if(!e.repeat&&!$('#submit').disabled)$('#chat-form').requestSubmit();
});
function questionHTML(question){
  return `<p class="submitted-question">${escapeHTML(question)}</p>`;
}
function renderConversation(question,result){
  closeReferences(false);
  currentSources=result.sources??[];
  const details=Object.values(result.context??{}).map(slot=>slot?.value).filter(Boolean).join('、');
  $('#result').innerHTML=questionHTML(question)+(details?questionHTML(details):'')+resultHTML(result);
  $('.workspace-transcript').scrollTop=0;
}
async function submitQuestion(context={},q=activeQuestion){
  if(!q||$('#submit').disabled)return;
  $('#question').value='';
  $('#submit').disabled=true;$('#new-session').disabled=true;$('#submit').textContent='正在处理…';
  closeReferences(false);
  showConversationTitle(q);
  $('#clarification-form').classList.add('hidden');
  $('#result').innerHTML=questionHTML(q)+'<div class="card request-progress" role="status"><span aria-hidden="true"></span>正在处理你的问题…</div>';
  try{
    const body={question:q,known_context:context};
    if(activeSession)Object.assign(body,{session_id:activeSession,revision:sessionRevision});
    const r=await api('agent',body);
    renderConversation(q,r);
    if(r.session_id)applySession(r);
  }catch(err){
    if(!activeSession&&!$('#question').value)$('#question').value=q;
    $('#clarification-form').classList.toggle('hidden',!missingFields.length);
    $('#result').innerHTML=questionHTML(q)+`<div class="card error">${escapeHTML(err.message)}。咨询冲突时请刷新恢复。</div>`;
  }
  finally{
    $('#submit').disabled=false;$('#new-session').disabled=false;
    $('#submit').textContent='发送 ↑';
    if(missingFields.length)$('#clarification-form').scrollIntoView({block:'nearest'});
    else $('.workspace-transcript').scrollTop=0;
  }
}
$('#import').onchange=async e=>{const f=e.target.files[0];if(!f)return;try{if(f.size>1900000)throw Error('文件超过大小限制');const v=JSON.parse(await f.text());const r=await api('documents',{documents:Array.isArray(v)?v:[v]});$('#import-message').textContent=`已导入 ${r.imported} 份文档。`;await load();}catch(err){$('#import-message').textContent=err.message;}e.target.value='';};
$('#run-eval').onclick=async()=>{
  $('#run-eval').disabled=true;$('#eval-result').textContent='正在执行患者流程验收…';
  try{
    const r=await api('patient/evaluate',{});
    $('#eval-result').innerHTML=`<p>五类工程场景：${r.five.passed} / ${r.five.total} · 开发问题流程：${r.development.passed} / ${r.development.total}</p><p>${escapeHTML(r.scope)}</p>`+
      r.five.cases.map(c=>`<div class="eval-row"><span>${escapeHTML(c.scenario)}</span><span>${c.passed?'✓ 通过':'× 失败'}</span></div>`).join('')+
      `<details><summary>查看40个开发问题的执行结果</summary>${r.development.cases.map(c=>`<p>${escapeHTML(c.id)} · ${escapeHTML(c.actual)} · ${c.passed?'通过':'失败'}</p>`).join('')}</details>`;
  }catch(e){$('#eval-result').textContent=e.message;}finally{$('#run-eval').disabled=false;}
};

$('#clarification-form').onsubmit=e=>{
  e.preventDefault();
  const data=new FormData(e.currentTarget);
  const context={};
  for(const key of missingFields){
    const choices=data.getAll(key);
    const typed=data.get(key+'_text')?.trim();
    const multi=['symptoms','associated_symptoms'].includes(key);
    const value=multi?[...choices,typed].filter(Boolean).join('、'):typed||choices[0];
    const input=e.currentTarget.elements.namedItem(key+'_text');
    if(value?.length>300&&input){
      input.setCustomValidity('选项与补充内容合计最多 300 字。');
      input.reportValidity();
      return;
    }
    if(value)context[key]=value;
  }
  if(!Object.keys(context).length){
    e.currentTarget.querySelector('input')?.focus();
    return;
  }
  submitQuestion(context);
};
$('#clarification-form').oninput=event=>{
  for(const input of event.currentTarget.querySelectorAll('input[type="text"],input:not([type])'))input.setCustomValidity('');
};
$('#new-session').onclick=newConsultation;

async function restoreConversation(id){
  if(!id||$('#submit').disabled)return;
  $('#submit').disabled=true;
  $('#new-session').disabled=true;
  $('#history-feedback').textContent='';
  try{
    const s=await api('session/'+id);
    if(!s.result)throw Error('咨询暂无可恢复结果');
    $('#question').value='';
    renderConversation(s.question,s.result);
    applySession({...s.result,session_id:s.session_id,revision:s.revision,original_question:s.question});
  }catch{
    if(!activeSession)sessionStorage.removeItem(sessionKey);
    $('#history-feedback').textContent='这条咨询暂时无法打开。';
  }finally{
    $('#submit').disabled=false;
    $('#new-session').disabled=false;
  }
}

$('#chat-history').onclick=event=>{
  const entry=event.target.closest('[data-session]');
  if(entry)restoreConversation(entry.dataset.session);
};
window.addEventListener('storage',event=>{
  if(event.key===historyKey){chatHistory=readHistory();renderHistory();}
});
renderHistory();
restoreConversation(sessionStorage.getItem(sessionKey));
load().catch(()=>{$('#engine').textContent='连接失败';});
