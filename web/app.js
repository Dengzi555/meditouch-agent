const $=s=>document.querySelector(s);const escapeHTML=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const contextLabels={population:'年龄段 / 人群',renal_function:'肾功能情况',symptoms:'主要症状',duration:'持续时间及变化',temperature:'体温及测量时间',temperature_method:'体温测量方式及部位',associated_symptoms:'伴随症状',risk_factors:'与本次问题相关的健康背景',drug_name:'咨询药品的完整名称及规格',drug_ingredients:'说明书标注的有效成分',medications:'已用药、剂量、服用时间及已知过敏',label_sodium:'标签钠含量及单位',label_basis:'标签每份基准',consumed_amount:'实际食用量'};
let activeSession=null,sessionRevision=0;
let activeQuestion='';
const historyKey='zhiheng.chat-history.v1';
const accountSignal='yidiantong.account-change';
let account=null;

const sidebarToggle=$('#sidebar-toggle');
const sidebarKey='yidiantong.sidebar-collapsed';
function setSidebarCollapsed(collapsed){
  document.body.classList.toggle('sidebar-collapsed',collapsed);
  sidebarToggle.setAttribute('aria-expanded',String(!collapsed));
  const label=collapsed?'展开侧栏':'收起侧栏';
  sidebarToggle.setAttribute('aria-label',label);
  sidebarToggle.title=label;
}
try{setSidebarCollapsed(localStorage.getItem(sidebarKey)==='true');}catch{}
sidebarToggle.onclick=()=>{
  const collapsed=sidebarToggle.getAttribute('aria-expanded')==='true';
  setSidebarCollapsed(collapsed);
  try{localStorage.setItem(sidebarKey,String(collapsed));}catch{}
};

function readHistory(){
  try{
    const entries=JSON.parse(localStorage.getItem(historyKey)??'[]');
    return Array.isArray(entries)?entries.filter(entry=>
      entry&&/^[a-f0-9]{32}$/.test(entry.id)&&typeof entry.title==='string'):[];
  }catch{return [];}
}

let chatHistory=[];
let selectingHistory=false;
let historyBusy=false;
let menuSession=null;
const selectedHistory=new Set();
const historyMenu=$('#history-menu');

function renderHistory(){
  historyMenu.hidePopover();
  const list=$('#chat-history');
  const scrollTop=list.scrollTop;
  for(const id of selectedHistory){
    if(!chatHistory.some(entry=>entry.id===id))selectedHistory.delete(id);
  }
  const row=entry=>`<li class="history-row">
    ${selectingHistory?`<input type="checkbox" data-select="${entry.id}" aria-label="选择 ${escapeHTML(entry.title)}" ${selectedHistory.has(entry.id)?'checked':''}>`:''}
    <button type="button" class="history-title" data-session="${entry.id}" title="${escapeHTML(entry.title)}" ${entry.id===activeSession?'aria-current="true"':''}>${escapeHTML(entry.title)}</button>
    ${selectingHistory?'':`<button type="button" class="history-more" data-more="${entry.id}" aria-label="管理 ${escapeHTML(entry.title)}" aria-controls="history-menu">⋯</button>`}
  </li>`;
  const pinned=chatHistory.filter(entry=>entry.pinned);
  const ordinary=chatHistory.filter(entry=>!entry.pinned);
  list.innerHTML=(pinned.length?'<li class="history-group-label">置顶</li>'+pinned.map(row).join(''):'')+
    (pinned.length&&ordinary.length?'<li class="history-group-label">最近</li>':'')+ordinary.map(row).join('');
  list.scrollTop=scrollTop;
  $('#history-batch').hidden=!selectingHistory;
  $('#history-select-all').textContent=chatHistory.length&&selectedHistory.size===chatHistory.length?'取消全选':'全选';
  const remove=$('#history-delete-selected');
  remove.disabled=!selectedHistory.size||historyBusy;
  remove.textContent=selectedHistory.size?`删除所选 (${selectedHistory.size})`:'删除所选';
}

function rememberConversation(){
  if(account){
    if(!chatHistory.some(entry=>entry.id===activeSession)){
      chatHistory.unshift({id:activeSession,title:activeQuestion.replace(/\s+/g,' ').slice(0,100)});
    }
    renderHistory();
    return;
  }
  const entries=new Map([...readHistory(),...chatHistory].map(entry=>[entry.id,entry]));
  chatHistory=entries.has(activeSession)?[...entries.values()]:
    [{id:activeSession,title:activeQuestion.replace(/\s+/g,' ').slice(0,100)},...entries.values()];
  try{localStorage.setItem(historyKey,JSON.stringify(chatHistory));}catch{}
  renderHistory();
}
function applySession(r){
  activeSession=r.session_id;sessionRevision=r.revision;
  activeQuestion=r.original_question??r.question;
  showConversationTitle(activeQuestion);
  rememberConversation();
}
function newConsultation(){
  activeSession=null;sessionRevision=0;
  displayedResult=null;
  activeQuestion='';
  $('#question').value='';
  closeReferences(false);
  $('#result').innerHTML='';showConversationTitle('');
  $('#history-feedback').textContent='';
  renderHistory();
  $('#submit').textContent='发送 ↑';$('#question').focus();
}
function showConversationTitle(question){
  $('#session-status').textContent=chatHistory.find(entry=>entry.id===activeSession)?.title||question||'新咨询';
}
async function api(path,data){
  const headers={};
  if(path==='agent'||path==='sessions'||path==='sessions/manage'||path.startsWith('session/')){
    headers['X-Account-Context']=account?.id??'guest';
  }
  const options={headers};
  if(data!==undefined){
    headers['Content-Type']='application/json';
    Object.assign(options,{method:'POST',body:JSON.stringify(data)});
  }
  const r=await fetch('/api/'+path,options);
  const v=await r.json();
  if(!r.ok)throw Error(v.error||'请求失败');
  return v;
}
function sourceLink(d, label, iconOnly=false){
  try{
    const url=new URL(d.source?.url??d.source_url);
    if(!['https:','http:'].includes(url.protocol))return '';
    const name=label??d.publisher??d.source?.publisher??url.hostname;
    return `<a class="source-chip" href="${escapeHTML(url.href)}" target="_blank" rel="noopener noreferrer" aria-label="${escapeHTML(name)}" title="${escapeHTML(name)}"><span aria-hidden="true">↗</span>${iconOnly?'':escapeHTML(name||url.hostname)}</a>`;
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
  const archived=trigger.closest('[data-prior-turn]')?.dataset.priorTurn;
  const sources=archived===undefined ? currentSources : responseSources(displayedPriorTurns[Number(archived)]);
  $('#reference-title').textContent=`${trigger.dataset.references==='related'?'检索到的资料':'参考资料'}（${sources.length}）`;
  $('#reference-list').innerHTML=sources.map((source,index)=>{
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
function answerText(text){
  return String(text??'').split(/\r?\n/).map(line=>line.trim())
    .filter(line=>!/^(?:文字来源|来源|作者|审核|编辑|责任编辑|责编|监制|供稿|转载自)\s*[:：]/.test(line))
    .map(line=>line.replace(/\[([^\]]+)\]\(https?:\/\/[^\s)]+\)/gi,'$1')
      .replace(/https?:\/\/[^\s，。！？；、]+/gi,'').trim()).filter(Boolean).join('\n');
}
function responseSources(result){
  if(['general_text','general_health','welcome','insufficient'].includes(result.status))return [];
  let sources=result.sources??[];
  if(['answered','guidance'].includes(result.status)){
    const used=new Set([...(result.citations??[]),...(result.answer_sections??[])
      .flatMap(section=>section.excerpts??[])].map(citation=>citation.evidence_id).filter(Boolean));
    sources=sources.filter(source=>used.has(source.id));
  }
  return [...sources,...[...(result.risk_references??[]),...(result.guidance_references??[])]
    .map(source=>({title:source.title,source_url:source.url}))];
}
function highlightHTML(text, highlight){
  const index=highlight ? text.indexOf(highlight) : -1;
  if(index<0)return escapeHTML(text);
  const end=index+highlight.length;
  return escapeHTML(text.slice(0,index))+
    `<strong class="answer-highlight">${escapeHTML(text.slice(index,end))}</strong>`+
    escapeHTML(text.slice(end));
}
function plainAnswerHTML(result){
  if(result.status==='general_text'){
    // Interpret only inline emphasis. Model output is always escaped.
    return String(result.answer??'').split(/(\*\*[^*\r\n]+\*\*)/g)
      .map((part,index)=>index%2
        ?`<strong class="answer-highlight">${escapeHTML(part.slice(2,-2))}</strong>`
        :escapeHTML(part)).join('');
  }
  const text=answerText(result.answer);
  const action=result.status==='risk_alert' ? text.split('\n')[0] : '';
  return highlightHTML(text,action);
}
function evidenceExcerpts(question, sources){
  question=question??'';
  const dietIntent=/(发烧|发热|感冒|流感|呼吸道感染)/.test(question)&&
    /(饮食|补水|喝水|饮水|食物|吃饭)/.test(question)&&!/(退烧药|用药|吃什么药|布洛芬|对乙酰氨基酚)/.test(question);
  const dietContent=/推荐的食物|少食多餐|补充水分|水分摄入|饮食|饮水|喝水/;
  const query=question.replace(/怎么办|怎么|如何|是不是|是什么|可以|能不能|有没有|吃什么|请问/g,' ');
  const terms = new Set(Array.from(query.matchAll(/(?=([\p{Script=Han}]{2}))/gu),match=>match[1]));
  const minimumMatches=Math.min(2,terms.size);
  return sources.map(source=>{
    if(dietIntent&&source.audience==='child'&&
       !/儿童|孩子|宝宝|婴幼儿|小儿|小孩|(?<!\d)(?:1[0-7]|[0-9])\s*岁|\d+\s*个月/.test(question))return null;
    const raw=answerText(source.raw_quote??source.text);
    const lines=raw.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
    const faqHeading=/^\d+[.．、]\s*.+[？?]$/;
    let blocks=lines;
    if(lines.filter(line=>faqHeading.test(line)).length>1){
      blocks=[];
      for(const [index,line] of lines.entries()){
        if(line.length<25&&!/[。！？?：:]$/.test(line)&&faqHeading.test(lines[index+1]??''))continue;
        if(faqHeading.test(line)||!blocks.length)blocks.push(line);
        else blocks[blocks.length-1]+='\n'+line;
      }
    }
    const candidates=blocks.filter(block=>block.length>=40&&block.length<=700&&/[。！？]$/.test(block)&&
      (!dietIntent||dietContent.test(block)));
    const ranked=candidates.map(excerpt=>({
      excerpt, matches:[...terms].filter(term=>excerpt.includes(term)).length,
    })).filter(item=>item.matches>0&&item.matches>=minimumMatches).sort((a,b)=>b.matches-a.matches);
    return ranked.length?{source,...ranked[0]}:null;
  }).filter(Boolean).sort((a,b)=>b.matches-a.matches).slice(0,2);
}
function clarificationHTML(card){
  const questions=(card.questions??[]).slice(0,1).map(question=>`
    <div class="clarify-question" data-qid="${escapeHTML(question.id)}" data-mode="${question.mode==='multi'?'multi':'single'}" role="group" aria-label="${escapeHTML(question.prompt)}">
      <h3 class="clarify-prompt"><span aria-hidden="true">💡</span> ${escapeHTML(question.prompt)}${question.mode==='multi'?' <span class="clarify-hint">可多选</span>':''}</h3>
      <div class="clarify-options">
        ${(question.options??[]).map(option=>`<button type="button" class="clarify-option" data-value="${escapeHTML(option.value)}" data-label="${escapeHTML(option.label)}" data-excludes="${escapeHTML(JSON.stringify(option.excludes??[]))}" ${option.exclusive?'data-exclusive="true"':''} aria-pressed="false">${escapeHTML(option.label)}</button>`).join('')}
      </div>
    </div>`).join('');
  return `<section class="card clarify-card" data-clarification data-symptom="${escapeHTML(card.symptom??'')}" data-field="${escapeHTML(card.field??'')}" data-round="${Number(card.round)||1}" aria-label="${escapeHTML(card.label)}追问">
    ${questions}
    <div class="clarify-actions"><button type="button" class="primary clarify-submit" disabled>继续 →</button><button type="button" class="clarify-skip">暂不补充</button></div>
  </section>`;
}
function resultHTML(r, showTrace=false){
  const labels={answered:'证据关联校验通过',risk_alert:'风险提醒 · 优先处理',needs_clarification:'需要补充条件',pending_review:'资料摘录',tool_error:'工具调用失败',model_error:'模型调用未完成',budget_exceeded:'已达到执行上限',validation_failed:'回答已被校验拦截',unresolved:'存在未解决分歧',evidence_found:'已找到结构化证据',insufficient:'证据不足',general_health:'通用医学知识 · 无检索证据'};
  const uncertainty=(r.uncertainty??[]).filter(x=>showTrace||! /^(仍未知的条件|用户声明尚未独立核实)/.test(x)).map(x=>`<p>${escapeHTML(x)}</p>`).join('');
  if(r.status==='needs_clarification'&&r.missing_context?.length){
    const fields=r.missing_context.map(key=>contextLabels[key]).filter(Boolean);
    const text=r.missing_context.includes('fever_intent')
      ? r.clarification_questions?.fever_intent
      : fields.length ? `你可以直接回复${fields.join('、')}。不知道的可以说不清楚。` : r.answer;
    r={...r,answer:text??r.answer};
  }
  const sources=responseSources(r);
  const verifiedQuotes=r.status==='answered'?(r.citations??[]).filter(c=>typeof c?.quote==='string'):[];
  const statements=r.status==='answered'&&!sources.some(source=>source.demo)?r.statements??[]:[];
  const preview=r.status==='pending_review';
  const extractive=preview||verifiedQuotes.length>0&&!statements.length&&!sources.some(source=>source.demo);
  const excerptSources=preview?sources:verifiedQuotes.flatMap(c=>{
    const source=sources.find(item=>item.id===c.evidence_id);
    return source?[{...source,raw_quote:c.quote}]:[];
  });
  const excerpts=extractive?evidenceExcerpts(r.question??r.original_question,excerptSources):[];
  const sections=(r.answer_sections??[]).map(section=>{
    const quotes=section.excerpts??[];
    const points=quotes.map(citation=>{
      const source=citation.source_url?{source_url:citation.source_url}:
        sources.find(item=>item.id===citation.evidence_id);
      const quote=answerText(citation.quote);
      const content=highlightHTML(quote,citation.highlight);
      return `<li>${content} ${source?sourceLink(source,undefined,true):''}</li>`;
    }).join('');
    const retrievalNotice={timeout:'这部分补查超时，可以稍后单独提问。',busy:'这部分补查暂时繁忙，可以稍后重试。',
      error:'这部分补查失败，可以稍后单独重试。'};
    const fallback=section.has_evidence?'相关原文见参考资料。':
      retrievalNotice[section.retrieval_status]??`目前缺少能直接支持「${section.title}」的适用资料。`;
    const nhs=sources.find(source=>source.attribution_logo==='https://assets.nhs.uk/nhsuk-cms/images/nhs-attribution.width-510.png'
      && quotes.some(quote=>quote.evidence_id===source.id)
      && /^https:\/\/www\.nhs\.uk\//.test(source.source_url));
    const attribution=nhs?`<p>英国 NHS 原文，服务号码及就医流程适用于英国。</p><a href="${escapeHTML(nhs.source_url)}" target="_blank" rel="noopener noreferrer"><img src="${nhs.attribution_logo}" width="250" height="49" alt="Content supplied by the NHS website"></a>`:'';
    return `<div class="answer-section"><h3>${escapeHTML(section.title)}</h3>${points?`<ul class="answer-points">${points}</ul>`:r.streaming?'':`<p>${escapeHTML(fallback)}</p>`}${attribution}</div>`;
  }).join('');
  const answer=sections || (extractive ? `<div class="answer-section">${excerpts.length?`<h3>相关证据要点</h3><ul class="answer-points">${excerpts.map(({source,excerpt})=>`<li>${escapeHTML(excerpt)} ${sourceLink(source,undefined,true)}</li>`).join('')}</ul>`:'现有资料没有找到能直接回答这个问题的段落。'}</div>` : statements.length ? statements.map(statement=>{
    const source=sources.find(item=>item.id===statement.evidence_id);
    return `<div class="answer-section"><p class="answer-point">${escapeHTML(answerText(statement.text))} ${source?sourceLink(source,undefined,true):''}</p><p>适用范围：${escapeHTML(answerText(statement.applicability))}</p><p>限制：${escapeHTML(answerText(statement.limitations))}</p></div>`;
  }).join('') : verifiedQuotes.length ? `<div class="answer">${sources.some(source=>source.demo)?'<p>虚构演示</p>':''}${verifiedQuotes.map(c=>{
    const source=sources.find(item=>item.id===c.evidence_id);
    return `<p>${escapeHTML(answerText(c.quote))} ${source?sourceLink(source,undefined,true):''}</p>`;
  }).join('')}</div>` : `<div class="answer">${plainAnswerHTML(r)}</div>`);
  const badge=['answered','guidance','welcome','general_text'].includes(r.status)?'':`<span class="badge warn">${preview&&!excerpts.length?'暂无法回答':labels[r.status]||escapeHTML(r.status)}</span>`;
  const references=sources.length?(showTrace
    ?`<details><summary>查看来源 · ${sources.length} 篇</summary>${sources.map(sourceHTML).join('')}</details>`
    :`<button type="button" class="reference-toggle" data-references="${preview?'related':'reference'}" aria-expanded="false" aria-controls="references">${preview?'查看检索结果':'查看参考资料'} · ${sources.length} 条 <span aria-hidden="true">›</span></button>`):'';
  const trace=showTrace?`<details><summary>执行轨迹 · ${escapeHTML(r.engine)} · ${r.latency_ms} ms</summary><pre>${escapeHTML(JSON.stringify(r.trace,null,2))}</pre></details>`:'';
  const followup=r.followup;
  const followupCard=followup?.question&&followup.options?.length?{
    label:'补充情况',field:followup.field,questions:[{id:followup.field,prompt:followup.question,
      mode:followup.field==='associated_symptoms'?'multi':'single',
      options:followup.options.map(label=>({value:label,label,excludes:followup.option_conflicts?.[label]??[]}))}],
  }:null;
  const clarify=r.status!=='risk_alert'&&(r.clarification_card||followupCard)
    ?clarificationHTML(r.clarification_card||followupCard):'';
  const known=r.answer_context;
  const change=known?.assessment_change;
  const changeHTML=change?`<p><strong>判断更新</strong> ${escapeHTML(change.after.quote)} ${change.after.source_url?sourceLink({source_url:change.after.source_url},undefined,true):''}</p><details><summary>查看上轮判断</summary><p>${escapeHTML(change.before.quote)}</p></details>`:'';
  const contextHTML=known?.known?.length?`<div class="answer-context"><p><strong>目前已知</strong> ${known.known.map(item=>escapeHTML(item.label+'：'+item.value)).join(' · ')}</p>${known.changed?.length?`<p><strong>本轮补充</strong> ${known.changed.map(item=>escapeHTML(item.label+'：'+item.value)).join(' · ')}</p>`:''}${changeHTML}</div>`:'';
  return `<article class="card chat-answer ${r.status==='risk_alert'?'urgent-answer':''}">${showTrace?'':references}${badge}${contextHTML}${answer}${uncertainty}${r.error&&r.error!==r.answer?`<p class="error">${escapeHTML(r.error)}</p>`:''}${showTrace?references:''}${trace}</article>${clarify}`;
}
async function load(){const state=await api('state');$('#engine').textContent=state.config.enabled?'模型已配置 · '+state.config.model:'规则演示模式 · 未配置模型';$('#documents').innerHTML=state.documents.map(sourceHTML).join('');}
$('#chat-form').onsubmit=e=>{
  e.preventDefault();
  const question=$('#question').value.trim();
  if(question){
    submitQuestion({},question,Boolean(activeSession));
  }
};
$('#question').addEventListener('keydown',e=>{
  if(e.key!=='Enter'||e.shiftKey||e.isComposing||e.keyCode===229)return;
  e.preventDefault();
  if(!e.repeat&&!$('#submit').disabled)$('#chat-form').requestSubmit();
});
function questionHTML(question){
  return `<p class="submitted-question">${escapeHTML(question)}</p>`;
}
let displayedResult=null;
let displayedPriorTurns=[];
function turnHTML(question,result){
  const details=Object.entries(result.context??{}).filter(([key])=>key!=='clarification')
    .map(([,slot])=>slot?.value).filter(Boolean).join('、');
  const summary=result.clarification_summary;
  const showSummary=summary&&!String(question).startsWith('已补充：');
  return questionHTML(question)+(showSummary?questionHTML('已补充：'+summary):details?questionHTML(details):'')+resultHTML(result);
}
function priorTurnsHTML(turns){
  displayedPriorTurns=turns;
  return turns.map((turn,index)=>`<div data-prior-turn="${index}">${turnHTML(turn.original_question??turn.question,turn)}</div>`).join('');
}
function renderConversation(question,result){
  closeReferences(false);
  displayedResult=result;
  currentSources=responseSources(result);
  const prior=result.prior_turns??[];
  $('#result').innerHTML=priorTurnsHTML(prior)+turnHTML(question,result);
  $('#result').querySelectorAll('[data-prior-turn] [data-clarification] button')
    .forEach(button=>{button.disabled=true;});
  const transcript=$('.workspace-transcript');
  transcript.scrollTop=prior.length?transcript.scrollHeight:0;
}

const routeCards={
  education:['健康科普','public_search','search','select','validate','web_search','general_health','answer'],
  consultation:['症状咨询','public_search','search','select','validate','answer'],
  guidance:['症状科普','answer'],
  clarification:['补充关键信息','answer'],
  risk:['风险提醒','answer'],
  insufficient:['适用范围检查','answer']
};
const stageLabels={intake:'识别问题',route:'确定处理路径',public_search:'检查公开资料',search:'检索本地知识库',select:'筛选证据',validate:'校验引用',web_search:'联网查阅官方原文',general_health:'生成通用科普',answer:'呈现结果'};
const routeStages={
  guidance:['匹配科普内容','显示科普要点'],
  clarification:['检查缺少的信息','显示补充选项'],
  risk:['检查风险信号','显示风险提醒'],
  insufficient:['检查支持范围','说明当前限制']
};
function renderProgress(route, stage, completed){
  const [title,...steps]=routeCards[route]??['正在识别问题'];
  const stages=['intake','route',...steps.filter(step=>step==='answer'||step===stage||completed.has(step))];
  const labels={...stageLabels};
  if(routeStages[route])[labels.route,labels.answer]=routeStages[route];
  const card=$('#result .request-progress');
  card.classList.toggle('risk-progress',route==='risk');
  if(route==='general_text'){
    card.innerHTML='<p role="status">正在回答…</p>';
    return;
  }
  card.innerHTML=`<strong>${title}</strong><p role="status">${labels[stage]}</p>
    <ol>${stages.map(step=>`<li class="${step===stage?'current':completed.has(step)?'complete':''}" ${step===stage?'aria-current="step"':''}>${labels[step]}</li>`).join('')}</ol>`;
}

async function streamConsultation(body, onEvent){
  const response=await fetch('/api/agent/stream',{
    method:'POST',
    headers:{'Content-Type':'application/json','X-Account-Context':account?.id??'guest'},
    body:JSON.stringify(body)
  });
  if(!response.ok)throw Error((await response.json()).error||'请求失败');
  const reader=response.body.pipeThrough(new TextDecoderStream()).getReader();
  let pending='';
  try{
    while(true){
      const {value,done}=await reader.read();
      if(done)throw Error('连接中断，未收到完整回答');
      pending+=value;
      const lines=pending.split('\n');
      pending=lines.pop();
      for(const line of lines){
        if(!line)continue;
        const event=JSON.parse(line);
        if(event.event==='error')throw Error(event.message);
        if(event.event==='done')return event.result;
        await onEvent(event);
      }
    }
  }finally{
    await reader.cancel();
  }
}

async function revealAnswerText(text, update){
  const characters=Array.from(text??'');
  const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
  if(document.hidden||reducedMotion.matches||characters.length<=20){
    update(characters.join(''));
    return;
  }
  const step=Math.max(12,Math.ceil(characters.length/12));
  for(let length=step;length<characters.length;length+=step){
    if(document.hidden||reducedMotion.matches)break;
    update(characters.slice(0,length).join(''));
    await new Promise(resolve=>setTimeout(resolve,24));
  }
  update(characters.join(''));
}

async function submitQuestion(context={},q=activeQuestion,appendTurn=false,displayQuestion=null){
  if(!q||$('#submit').disabled)return;
  const shown=displayQuestion||q;
  $('#question').value='';
  $('#submit').disabled=true;$('#new-session').disabled=true;$('#submit').textContent='正在处理…';
  closeReferences(false);
  showConversationTitle(shown);
  const earlier=[...(displayedResult?.prior_turns??[])];
  if(appendTurn&&displayedResult)earlier.push(displayedResult);
  $('#result').innerHTML=priorTurnsHTML(earlier)+questionHTML(shown)+'<div class="card request-progress"></div><div class="stream-answer"></div>';
  $('.workspace-transcript').scrollTop=$('.workspace-transcript').scrollHeight;
  let route, stage='intake', partial;
  const completed=new Set();
  renderProgress(route,stage,completed);
  try{
    const body={question:q,known_context:context};
    if(activeSession)Object.assign(body,{session_id:activeSession,revision:sessionRevision});
    if(appendTurn)body.append_turn=true;
    const r=await streamConsultation(body,async event=>{
      if(event.event==='route')route=event.route;
      if(event.event in stageLabels){
        if(stage!==event.event)completed.add(stage);
        stage=event.event;
        renderProgress(route,stage,completed);
      }
      if(event.event==='answer'){
        partial={status:event.status,sources:event.sources,citations:event.citations,risk_references:event.risk_references,
          guidance_references:event.guidance_references,answer_context:event.answer_context,answer_sections:[],answer:'',streaming:true};
      }
      function renderPartial(){
        currentSources=responseSources(partial);
        $('.stream-answer').innerHTML=resultHTML(partial);
      }
      if(event.event==='section'){
        partial.answer_sections.push(event.section);
        renderPartial();
      }else if(event.event==='point'){
        const point={...event.point,quote:''};
        partial.answer_sections.at(-1).excerpts.push(point);
        await revealAnswerText(event.point.quote, text=>{
          point.quote=text;
          renderPartial();
        });
      }else if(event.event==='paragraph'){
        const prefix=partial.answer?partial.answer+'\n\n':'';
        await revealAnswerText(event.text,text=>{
          partial.answer=prefix+text;
          renderPartial();
        });
      }
    });
    renderConversation(shown,r);
    if(r.session_id)applySession(r);
  }catch(err){
    if(!activeSession&&!$('#question').value)$('#question').value=q;
    $('#result').innerHTML=questionHTML(shown)+`<div class="card error">${escapeHTML(err.message)}。咨询冲突时请刷新恢复。</div>`;
  }
  finally{
    $('#submit').disabled=false;$('#new-session').disabled=false;
    $('#submit').textContent='发送 ↑';
    if(!displayedResult?.prior_turns?.length)$('.workspace-transcript').scrollTop=0;
  }
}
function clarificationAnswers(card){
  const answers={};
  for(const group of card.querySelectorAll('.clarify-question')){
    const selected=[...group.querySelectorAll('.clarify-option[aria-pressed="true"]')].map(button=>button.dataset.value);
    if(!selected.length)return null;
    answers[group.dataset.qid]=group.dataset.mode==='multi'?selected:selected[0];
  }
  return answers;
}
function updateClarify(card){
  const ready=[...card.querySelectorAll('.clarify-question')].every(group=>
    group.querySelector('.clarify-option[aria-pressed="true"]'));
  const button=card.querySelector('.clarify-submit');
  if(button)button.disabled=!ready;
}
function submitClarification(card){
  if(!card||card.dataset.submitted==='1'||$('#submit').disabled)return;
  const answers=clarificationAnswers(card);
  if(!answers)return;
  const labels=[...card.querySelectorAll('.clarify-option[aria-pressed="true"]')].map(button=>button.dataset.label);
  const payload=card.dataset.field?{[card.dataset.field]:labels.join('、')}:
    {clarification:JSON.stringify({v:1,symptom:card.dataset.symptom,round:Number(card.dataset.round)||1,answers})};
  card.dataset.submitted='1';
  card.querySelectorAll('button').forEach(button=>{button.disabled=true;});
  submitQuestion(payload,
    displayedResult?.original_question||activeQuestion,false,'已补充：'+labels.join('、'));
}
document.addEventListener('click',event=>{
  const skip=event.target.closest?.('.clarify-skip');
  if(skip){skip.closest('[data-clarification]').remove();return;}
  const option=event.target.closest?.('.clarify-option');
  if(option){
    const group=option.closest('.clarify-question');
    const card=option.closest('[data-clarification]');
    if(!group||!card||option.disabled)return;
    const pressed=option.getAttribute('aria-pressed')==='true';
    if(group.dataset.mode!=='multi'){
      group.querySelectorAll('.clarify-option').forEach(button=>button.setAttribute('aria-pressed','false'));
      option.setAttribute('aria-pressed',String(!pressed));
    }else if(option.dataset.exclusive==='true'){
      group.querySelectorAll('.clarify-option').forEach(button=>button.setAttribute('aria-pressed','false'));
      option.setAttribute('aria-pressed','true');
    }else{
      option.setAttribute('aria-pressed',String(!pressed));
      if(!pressed)group.querySelectorAll('.clarify-option[data-exclusive="true"]')
        .forEach(button=>button.setAttribute('aria-pressed','false'));
    }
    if(!pressed){
      const excludes=JSON.parse(option.dataset.excludes||'[]');
      group.querySelectorAll('.clarify-option').forEach(button=>{
        if(excludes.includes(button.dataset.value))button.setAttribute('aria-pressed','false');
      });
    }
    updateClarify(card);
    return;
  }
  const submit=event.target.closest?.('.clarify-submit');
  if(submit)submitClarification(submit.closest('[data-clarification]'));
});
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
    const staleIntake=s.result.status==='needs_clarification'&&s.result.missing_context?.length>0&&s.missing_context?.length===0;
    const result=staleIntake?{...s.result,status:'guidance',missing_context:[],
      answer:'这条咨询可以直接继续。点击「重新回答」查看更新后的回复。'}:s.result;
    renderConversation(s.question,{...result,prior_turns:s.prior_turns??[]});
    applySession({...result,session_id:s.session_id,revision:s.revision,original_question:s.question});
    if(staleIntake){
      const retry=document.createElement('button');
      retry.type='button';
      retry.className='primary';
      retry.textContent='重新回答';
      retry.onclick=()=>submitQuestion();
      $('#result .chat-answer').append(retry);
    }
  }catch{
    $('#history-feedback').textContent='这条咨询暂时无法打开。';
  }finally{
    $('#submit').disabled=false;
    $('#new-session').disabled=false;
  }
}

function setHistorySelection(enabled){
  selectingHistory=enabled;
  selectedHistory.clear();
  renderHistory();
}
async function manageHistory(action,ids,value){
  if(historyBusy||$('#submit').disabled||!ids.length)return;
  if(action==='delete'){
    const dialog=$('#history-delete-dialog');
    if(dialog.open)return;
    $('#history-delete-title').textContent=ids.length===1?'删除后，该对话将不可恢复':`删除后，这 ${ids.length} 条对话将不可恢复`;
    $('#history-delete-confirm').textContent=ids.length===1?'删除该对话':'删除所选对话';
    dialog.returnValue='';
    const confirmed=new Promise(resolve=>{
      dialog.addEventListener('close',()=>resolve(dialog.returnValue==='delete'),{once:true});
    });
    dialog.showModal();
    if(!await confirmed)return;
  }
  historyBusy=true;
  $('#history-feedback').textContent='';
  renderHistory();
  try{
    const {entries}=await api('sessions/manage',{action,ids,value});
    const replacements=new Map(entries.map(entry=>[entry.id,entry]));
    const apply=history=>action==='delete'?history.filter(entry=>!ids.includes(entry.id)):
      history.map(entry=>replacements.get(entry.id)??entry);
    if(account){
      chatHistory=apply(chatHistory);
    }else{
      chatHistory=apply(readHistory());
      localStorage.setItem(historyKey,JSON.stringify(chatHistory));
    }
    if(action==='delete'){
      selectingHistory=false;
      selectedHistory.clear();
      if(ids.includes(activeSession))newConsultation();
    }
    showConversationTitle(activeQuestion);
  }catch(error){
    $('#history-feedback').textContent=error.message==='SESSION_BUSY'?'这条咨询正在处理，请稍后再试。':'聊天记录操作失败，请刷新后重试。';
  }finally{
    historyBusy=false;
    renderHistory();
  }
}
$('#history-cancel-selection').onclick=()=>setHistorySelection(false);
$('#history-select-all').onclick=()=>{
  if(selectedHistory.size===chatHistory.length)selectedHistory.clear();
  else for(const entry of chatHistory)selectedHistory.add(entry.id);
  renderHistory();
};
$('#history-delete-selected').onclick=()=>manageHistory('delete',[...selectedHistory]);
$('#chat-history').onclick=event=>{
  const more=event.target.closest('[data-more]');
  if(more){
    if(historyBusy)return;
    if(historyMenu.matches(':popover-open')&&menuSession===more.dataset.more){
      historyMenu.hidePopover();
      return;
    }
    menuSession=more.dataset.more;
    const entry=chatHistory.find(item=>item.id===menuSession);
    historyMenu.querySelector('[data-action="pin"]').textContent=entry.pinned?'取消置顶':'置顶';
    historyMenu.showPopover();
    const bounds=more.getBoundingClientRect();
    historyMenu.style.left=Math.max(8,Math.min(bounds.right-historyMenu.offsetWidth,innerWidth-historyMenu.offsetWidth-8))+'px';
    historyMenu.style.top=Math.max(8,Math.min(bounds.bottom+4,innerHeight-historyMenu.offsetHeight-8))+'px';
    return;
  }
  const id=event.target.closest('[data-session]')?.dataset.session??event.target.dataset.select;
  if(!id)return;
  if(selectingHistory){
    if(selectedHistory.has(id))selectedHistory.delete(id);
    else selectedHistory.add(id);
    renderHistory();
  }else if(!historyBusy)restoreConversation(id);
};
historyMenu.onclick=event=>{
  const action=event.target.closest('[data-action]')?.dataset.action;
  const entry=chatHistory.find(item=>item.id===menuSession);
  if(!action||!entry)return;
  historyMenu.hidePopover();
  if(action==='select'){
    setHistorySelection(true);
    selectedHistory.add(entry.id);
    renderHistory();
  }else if(action==='rename'){
    editHistoryTitle(entry);
  }else{
    manageHistory(action,[entry.id],!entry.pinned);
  }
};
function editHistoryTitle(entry){
  if(historyBusy||$('#submit').disabled)return;
  const row=$(`[data-session="${entry.id}"]`)?.closest('.history-row');
  if(!row)return;
  const input=document.createElement('input');
  input.type='text';
  input.className='history-title-edit';
  input.setAttribute('aria-label','聊天记录名称');
  input.maxLength=100;
  input.value=entry.title;
  row.replaceChildren(input);

  let finished=false;
  function finish(save,restoreFocus=false){
    if(finished||!input.isConnected)return;
    finished=true;
    const title=input.value.trim().replace(/\s+/g,' ');
    const saved=save&&title&&title!==entry.title
      ?manageHistory('rename',[entry.id],title)
      :Promise.resolve(renderHistory());
    if(restoreFocus)saved.then(()=>$('[data-session="'+entry.id+'"]')?.focus());
  }
  input.addEventListener('blur',()=>finish(true));
  input.addEventListener('keydown',event=>{
    if(event.isComposing||event.keyCode===229)return;
    if(event.key==='Enter'||event.key==='Escape'){
      event.preventDefault();
      finish(event.key==='Enter',true);
    }
  });
  input.focus();
  input.select();
}
window.addEventListener('storage',event=>{
  if(event.key===accountSignal)location.reload();
  if(!account&&event.key===historyKey){chatHistory=readHistory();renderHistory();}
});
const accountForm=$('#account-form');
const logoutButton=$('#account-logout');
const accountButton=$('#account-button');
const accountOptions=$('#account-options');
function setAccountOptions(open){
  accountOptions.hidden=!open;
  accountButton.setAttribute('aria-expanded',String(open));
}
function openAccountDialog(){
  $('#account-title').textContent=account?account.username:'账号登录';
  accountForm.hidden=Boolean(account);
  $('#account-error').textContent='';
  accountForm.reset();
  $('#account-dialog').showModal();
}
accountButton.onclick=()=>{
  if(account) setAccountOptions(accountOptions.hidden);
  else openAccountDialog();
};
$('#account-profile').onclick=()=>{
  setAccountOptions(false);
  accountButton.focus();
  openAccountDialog();
};
document.addEventListener('click',event=>{
  if(account&&!event.target.closest('.account-area')) setAccountOptions(false);
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&!accountOptions.hidden){
    setAccountOptions(false);
    accountButton.focus();
  }
});
async function changeAccount(action,data){
  const controls=[...accountForm.querySelectorAll('button'),logoutButton];
  controls.forEach(button=>button.disabled=true);
  const errorText=$(action==='logout'?'#account-menu-error':'#account-error');
  errorText.textContent='';
  try{
    await api('account/'+action,data);
    try{localStorage.setItem(accountSignal,String(Date.now()));}catch{}
    location.reload();
  }catch(error){
    errorText.textContent=error.message;
    controls.forEach(button=>button.disabled=false);
  }
}
accountForm.onsubmit=event=>{
  event.preventDefault();
  changeAccount(event.submitter?.value||'login',Object.fromEntries(new FormData(accountForm)));
};
logoutButton.onclick=()=>changeAccount('logout',{});

async function initializeAccount(){
  $('#submit').disabled=true;
  $('#new-session').disabled=true;
  try{
    ({account}=await api('account'));
    if(account){
      $('#account-label').textContent=account.username;
      $('.account-avatar').hidden=false;
      $('.account-chevron').hidden=false;
      accountButton.removeAttribute('aria-haspopup');
      accountButton.setAttribute('aria-controls','account-options');
      setAccountOptions(false);
      chatHistory=await api('sessions');
    }else{
      chatHistory=readHistory();
    }
    renderHistory();
    $('#account-button').disabled=false;
    $('#submit').disabled=false;
    $('#new-session').disabled=false;
  }catch(error){
    $('#history-feedback').textContent='账号状态读取失败，请刷新重试。';
  }
}
initializeAccount();
load().catch(()=>{$('#engine').textContent='连接失败';});
