const $ = (id) => document.getElementById(id);
const nativeFetch = window.fetch.bind(window);
const configuredApiOrigin = localStorage.getItem('claudex_remote_url') || '';
const configuredApiToken = localStorage.getItem('claudex_remote_token') || '';
if (configuredApiOrigin && configuredApiToken) {
  window.fetch = (input, init={}) => {
    const sourceUrl = input instanceof Request ? input.url : String(input);
    const parsed = new URL(sourceUrl, location.href);
    if (!parsed.pathname.startsWith('/api/')) return nativeFetch(input, init);
    const target = new URL(parsed.pathname + parsed.search, configuredApiOrigin);
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(init.headers || {}).forEach((value, key) => headers.set(key, value));
    headers.set('Authorization', `Bearer ${configuredApiToken}`);
    const request = input instanceof Request ? new Request(target.href, input) : target.href;
    return nativeFetch(request, {...init, headers});
  };
}
let activeConversation = null;
let isSending = false;
let currentAssistantText = '';
const mathTimers = new WeakMap();
let researchMode = false;
let councilMode = false;
let modelCatalog = {};
let selectedModel = '';
let selectedEffort = 'Medium';
let pendingAttachments = [];
let isUploadingFiles = false;
const MAX_USER_MESSAGE_CHARS = 1_000_000;

function toast(message, error=false) {
  const node=document.createElement('div');node.className='toast'+(error?' error':'');node.textContent=message;$('toastStack').appendChild(node);
  setTimeout(()=>node.remove(),3800);
}
function showModal(id){$(id).classList.add('open');$(id).setAttribute('aria-hidden','false');}
function hideModal(id){$(id).classList.remove('open');$(id).setAttribute('aria-hidden','true');}
let deleteConfirmResolver=null;
function confirmDeleteChat(title){$('deleteConfirmTitle').textContent='Delete this chat?';$('deleteConfirmName').textContent=title||'Chat';showModal('deleteConfirmModal');requestAnimationFrame(()=>$('cancelDeleteChat').focus());return new Promise(resolve=>{deleteConfirmResolver=resolve;});}
function settleDeleteChatConfirmation(confirmed){hideModal('deleteConfirmModal');if(deleteConfirmResolver){const resolve=deleteConfirmResolver;deleteConfirmResolver=null;resolve(confirmed);}}
function greeting(){const hour=new Date().getHours();const part=hour<12?'morning':hour<18?'afternoon':'evening';const name=(localStorage.getItem('claudex_display_name')||'').trim();return `Good ${part}${name?', '+name:''}.`;}
function modelName(model){return modelCatalog[model]?.name||model?.replace(/^nvidia\//,'')||'Choose model';}
function defaultEffort(model){const efforts=modelCatalog[model]?.efforts||['Medium'];return efforts.includes('Medium')?'Medium':efforts.includes('High')?'High':efforts[efforts.length-1];}
function setComposerModel(model){selectedModel=model;$('modelPickerName').textContent=modelName(model);$('modelEffortLabel').textContent=selectedEffort;$('modelPicker').title=modelName(model)+' · '+selectedEffort+' effort';}
function effortExplanation(model){
  if(model==='nvidia/nemotron-3.5-lightning-30b-a3b')return 'Choose how many reasoning tokens NVIDIA Nemotron can use. Larger budgets may improve difficult tasks and take longer.';
  if(model==='nvidia/nemotron-3-ultra-550b-a55b')return 'Choose Nemotron Ultra’s documented reasoning mode. Higher effort can take longer.';
  if(model==='nvidia/nemotron-3.5-super-vl-120b-a12b')return 'Choose Super VL’s documented reasoning mode. Higher effort can take longer.';
  return 'Turn Bolt’s documented reasoning mode on or off.';
}
function renderModelMenu(){
  const list=$('modelOptions');list.innerHTML='';Object.entries(modelCatalog).forEach(([id,detail])=>{
    const button=document.createElement('button');button.type='button';button.className='model-option'+(id===selectedModel?' selected':'');button.setAttribute('role','option');button.setAttribute('aria-selected',String(id===selectedModel));
    const name=document.createElement('span');name.className='model-option-name';name.textContent=detail.name;
    const description=document.createElement('span');description.className='model-option-description';description.textContent=detail.description;
    const check=document.createElement('span');check.className='model-option-check';check.textContent=id===selectedModel?'✓':'';button.append(name,description,check);button.addEventListener('click',()=>setSelectedModel(id));list.appendChild(button);
  });
}
function closePickers(){ $('modelMenu').hidden=true;$('effortMenu').hidden=true;$('modelPicker').setAttribute('aria-expanded','false');$('effortPicker').setAttribute('aria-expanded','false'); }
function autoResize(){const t=$('promptInput');t.style.height='auto';t.style.height=Math.min(t.scrollHeight,190)+'px';}
function updateSendState(){const value=$('promptInput').value;const chars=Array.from(value).length;if(chars>MAX_USER_MESSAGE_CHARS){$('promptInput').value=Array.from(value).slice(0,MAX_USER_MESSAGE_CHARS).join('');toast('Messages can contain up to 1,000,000 characters.',true);}const ready=($('promptInput').value.trim().length>0||pendingAttachments.length>0)&&!isSending&&!isUploadingFiles;$('sendBtn').disabled=!ready;$('sendBtn').classList.toggle('has-text',ready);}
function renderPendingAttachments(){
  const tray=$('attachmentTray');tray.innerHTML='';
  pendingAttachments.forEach(file=>{const chip=document.createElement('div');chip.className='attachment-chip';const icon=document.createElement('span');icon.className='attachment-file-icon';icon.innerHTML='<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 2.5h6l4 4v11H5zM11 2.5v4h4"/></svg>';const name=document.createElement('span');name.textContent=file.filename;name.title=file.filename;const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.title='Remove file';remove.setAttribute('aria-label','Remove '+file.filename);remove.addEventListener('click',()=>removePendingAttachment(file.id));chip.append(icon,name,remove);tray.appendChild(chip);});
  if(isUploadingFiles){const status=document.createElement('div');status.className='attachment-uploading';status.textContent='Reading files…';tray.appendChild(status);}
  tray.hidden=tray.childElementCount===0;updateSendState();
}
function addMessageAttachments(container,files){
  if(!files?.length)return;const tray=document.createElement('div');tray.className='message-attachments';
  files.forEach(file=>{const chip=document.createElement('span');chip.className='message-attachment-chip';chip.textContent=file.filename;tray.appendChild(chip);});container.appendChild(tray);
}
async function uploadSelectedFiles(fileList){
  const files=Array.from(fileList||[]);if(!files.length)return;
  if(pendingAttachments.length+files.length>5){toast('A message can include up to five files.',true);return;}
  isUploadingFiles=true;renderPendingAttachments();
  try{
    const conversationId=await ensureConversation();const form=new FormData();files.forEach(file=>form.append('files',file,file.name));
    const response=await fetch(`/api/conversations/${conversationId}/attachments`,{method:'POST',body:form});const data=await response.json();
    if(!response.ok)throw new Error(data.detail||'Could not read the selected files.');
    pendingAttachments.push(...(data.attachments||[]));renderPendingAttachments();await loadConversations();
  }catch(error){toast(String(error.message||error),true);}
  finally{isUploadingFiles=false;renderPendingAttachments();}
}
async function removePendingAttachment(id){
  const response=await fetch(`/api/conversations/${activeConversation}/attachments/${id}`,{method:'DELETE'});
  if(!response.ok){toast('Could not remove this attachment.',true);return;}
  pendingAttachments=pendingAttachments.filter(file=>file.id!==id);renderPendingAttachments();await loadConversations();
}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function normalizePathFormatting(text){const path='(?:\\/[\\w.-]+(?:\\/[\\w.-]+)*\\/?|(?:\\.{1,2}\\/)?[\\w.-]+(?:\\/[\\w.-]+)*\\.[\\w.-]+)';const inline=new RegExp('[\\[(]\\s*((?:`'+path+'`)(?:\\s*,\\s*`'+path+'`)*)\\s*[\\])](?!\\s*\\()','g');const plain=new RegExp('\\(\\s*('+path+'(?:\\s*,\\s*'+path+')*)\\s*\\)','g');return String(text||'').replace(inline,'$1').replace(plain,'$1');}
function safeMarkdown(text){
  text=normalizePathFormatting(text);
  const mathExpressions=[];
  const mathPattern=/\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$\$[\s\S]+?\$\$|(?<!\\)\$(?!\s)[^$\n]+?(?<!\s)\$(?!\$)/g;
  text=String(text||'').replace(mathPattern,expression=>{const token=`CLAUDEXMATHPLACEHOLDER${mathExpressions.length}END`;mathExpressions.push([token,expression]);return token;});
  if(!window.marked||!window.DOMPurify){let fallback=escapeHtml(text).replaceAll('\n','<br>');for(const [token,expression] of mathExpressions)fallback=fallback.replaceAll(token,escapeHtml(expression));return fallback;}
  const raw=window.marked.parse(text,{gfm:true,breaks:false});
  const clean=window.DOMPurify.sanitize(raw,{USE_PROFILES:{html:true}});
  const holder=document.createElement('template');holder.innerHTML=clean;
  const walker=document.createTreeWalker(holder.content,NodeFilter.SHOW_TEXT);const textNodes=[];while(walker.nextNode())textNodes.push(walker.currentNode);
  for(const node of textNodes){let value=node.nodeValue||'';for(const [token,expression] of mathExpressions)value=value.replaceAll(token,expression);node.nodeValue=value;}
  holder.content.querySelectorAll('table').forEach(table=>{const rows=Array.from(table.rows);const columns=Math.max(1,...rows.map(row=>row.cells.length));const header=table.querySelector('thead tr');const firstHeading=header?.cells[0]?.textContent.trim()||'';const numericFirstColumn=rows.slice(header?1:0).length>0&&rows.slice(header?1:0).every(row=>/^\d+$/.test(row.cells[0]?.textContent.trim()||''));const narrowFirstColumn=firstHeading==='#'||numericFirstColumn;table.style.setProperty('--table-min-width',`${columns*8-(narrowFirstColumn?5:0)}rem`);table.style.setProperty('--table-first-column',narrowFirstColumn?'3rem':'8rem');const wrap=document.createElement('div');wrap.className='table-scroll';table.parentNode.insertBefore(wrap,table);wrap.appendChild(table);});
  holder.content.querySelectorAll('pre').forEach(pre=>{const code=pre.querySelector('code');if(!code)return;const panel=document.createElement('div');panel.className='code-block-panel';pre.parentNode.insertBefore(panel,pre);panel.appendChild(pre);const button=document.createElement('button');button.type='button';button.className='code-copy-button';button.title='Copy code';button.setAttribute('aria-label','Copy code block');button.innerHTML='<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 6V4.8A1.8 1.8 0 0 1 8.8 3h6.4A1.8 1.8 0 0 1 17 4.8v8.4a1.8 1.8 0 0 1-1.8 1.8H14M5 7h6.2A1.8 1.8 0 0 1 13 8.8v6.4a1.8 1.8 0 0 1-1.8 1.8H4.8A1.8 1.8 0 0 1 3 15.2V8.8A1.8 1.8 0 0 1 4.8 7Z"/></svg>';button.addEventListener('click',()=>copyMessage(code.innerText||code.textContent||''));panel.appendChild(button);});
  holder.content.querySelectorAll('a').forEach(a=>{
    a.target='_blank';a.rel='noopener noreferrer';
    const originalLabel=a.textContent.trim();
    const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');
    icon.setAttribute('viewBox','0 0 16 16');icon.setAttribute('aria-hidden','true');icon.setAttribute('focusable','false');icon.classList.add('link-external-icon');
    const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d','M4 12 12 4M5 4h7v7');icon.appendChild(path);
    try{
      const url=new URL(a.href);
      if(url.protocol==='http:'||url.protocol==='https:'){
        const isOpaqueSource=/^S\d+(?:\s+S\d+)*$/.test(originalLabel);
        const readableName=url.hostname.replace(/^www\./,'');
        if(isOpaqueSource){a.classList.add('source-citation');a.textContent=readableName;}
        const preview=document.createElement('span');preview.className='link-preview';preview.setAttribute('aria-hidden','true');
        const name=document.createElement('span');name.className='link-preview-name';name.textContent=isOpaqueSource?`Source: ${readableName}`:readableName;
        const address=document.createElement('span');address.className='link-preview-address';address.textContent=url.href;
        preview.append(name,address);a.append(icon,preview);a.setAttribute('aria-label',`${isOpaqueSource?'Source: ':''}${isOpaqueSource?readableName:originalLabel} — ${url.hostname}: ${url.href}`);
      }else a.appendChild(icon);
    }catch{a.appendChild(icon);}
  });return holder.innerHTML;
}
function typeset(node){if(!window.MathJax||!node)return;clearTimeout(mathTimers.get(node));const timer=setTimeout(()=>{if(!node.isConnected)return;const pending=window.MathJax.typesetPromise?.([node]);pending?.catch(()=>{});},350);mathTimers.set(node,timer);}

function scrollFeed(){const feed=$('messageFeed');feed.scrollTop=feed.scrollHeight;}
function showWelcome(){
  document.body.classList.add('welcome-mode');
  $('messageFeed').innerHTML='';
  const panel=document.createElement('section');panel.className='welcome-screen';panel.id='welcomeScreen';
  const heading=document.createElement('h1');heading.id='welcomeGreeting';const mark=document.createElement('img');mark.className='welcome-claude-mark';mark.src='/static/claude-mark.svg?v=20261116-claude-blue';mark.alt='';mark.setAttribute('aria-hidden','true');const greetingText=document.createElement('span');greetingText.textContent=greeting();heading.append(mark,greetingText);
  panel.append(heading);$('messageFeed').appendChild(panel);
  $('workspaceTitle').textContent='New chat';
}
function messageAction(name,label,path,handler){const button=document.createElement('button');button.type='button';button.className='message-action '+name;button.title=label;button.setAttribute('aria-label',label);button.innerHTML=`<svg viewBox="0 0 20 20" aria-hidden="true"><path d="${path}"/></svg>`;button.addEventListener('click',handler);return button;}
function addMessageActions(message){
  const actions=document.createElement('div');actions.className='message-actions';
  if(message.role==='user'){
    const retry=messageAction('retry-message','Retry from this message','M4 4v5h5M4.8 8A6 6 0 1 1 4 12',()=>retryFromMessage(message));
    const edit=messageAction('edit-message','Edit message','m13.5 4.5 2 2M4 16l3.5-.7L16 6.8a1.4 1.4 0 0 0-2-2l-8.5 8.5L4 16Z',()=>beginMessageEdit(message));
    const copy=messageAction('copy-message','Copy message','M7 7h9v10H7zM4 13V4h9',()=>copyMessage(message.text.textContent));actions.append(retry,edit,copy);
  }else{
    const copy=messageAction('copy-message','Copy response','M7 7h9v10H7zM4 13V4h9',()=>copyMessage(message.text.innerText));actions.append(copy);
  }
  message.actions=actions;message.body.appendChild(actions);
}
async function copyMessage(text){try{await navigator.clipboard.writeText(text);toast('Copied to clipboard.');}catch{toast('Clipboard access was blocked by the browser.',true);}}
function beginMessageEdit(message){
  if(!message.messageId||message.editor)return;message.text.hidden=true;
  const editor=document.createElement('div');editor.className='message-editor';
  const input=document.createElement('textarea');input.value=message.text.textContent;input.setAttribute('aria-label','Edit your message');input.addEventListener('input',()=>{const chars=Array.from(input.value);if(chars.length>MAX_USER_MESSAGE_CHARS)input.value=chars.slice(0,MAX_USER_MESSAGE_CHARS).join('');});
  const footer=document.createElement('div');footer.className='message-edit-footer';const note=document.createElement('span');note.textContent='Edits remove later messages in this chat.';
  const cancel=document.createElement('button');cancel.type='button';cancel.textContent='Cancel';cancel.addEventListener('click',()=>{editor.remove();message.text.hidden=false;message.editor=null;});
  const save=document.createElement('button');save.type='button';save.className='save-edit';save.textContent='Save';save.addEventListener('click',()=>{const value=input.value;if(!value.trim()){toast('Message cannot be empty.',true);return;}editor.remove();message.text.hidden=false;message.editor=null;message.text.textContent=value;sendMessage({messageId:message.messageId,editedText:value,userRow:message});});
  footer.append(note,cancel,save);editor.append(input,footer);message.text.insertAdjacentElement('afterend',editor);message.editor=editor;input.focus();input.setSelectionRange(input.value.length,input.value.length);
}
function retryFromMessage(message){if(message.messageId)sendMessage({messageId:message.messageId,editedText:message.text.textContent,userRow:message});}
function createMessage(role,content='',attachments=[],messageId=null){
  $('welcomeScreen')?.remove();document.body.classList.remove('welcome-mode');
  const row=document.createElement('article');row.className='message-row '+role;
  const avatar=document.createElement('div');avatar.className='message-avatar'+(role==='assistant'?' assistant-avatar':'');
  if(role==='assistant')avatar.innerHTML='<span class="orbit-dot orbit-one"></span><span class="orbit-dot orbit-two"></span><span class="orbit-dot orbit-three"></span>';
  const body=document.createElement('div');body.className='message-body';
  const label=document.createElement('div');label.className='message-label';label.textContent=role==='assistant'?'ClaudeX':'';
  const trace=document.createElement('section');trace.className='research-activity';trace.hidden=true;
  const text=document.createElement('div');text.className='message-text';
  if(role==='user')text.textContent=content;else{text.innerHTML=safeMarkdown(content);typeset(text);}
  body.append(label,trace,text);if(role==='user')addMessageAttachments(body,attachments);row.append(avatar,body);$('messageFeed').appendChild(row);scrollFeed();
  const phase=document.createElement('div');phase.className='research-phase';
  const toggle=document.createElement('button');toggle.className='research-toggle';toggle.type='button';toggle.setAttribute('aria-expanded','false');
  const summary=document.createElement('span');summary.className='research-summary';summary.textContent='Research activity';
  const chevron=document.createElement('span');chevron.className='research-chevron';chevron.textContent='⌄';toggle.append(summary,chevron);
  const panel=document.createElement('div');panel.className='research-panel';panel.hidden=true;trace.append(phase,toggle,panel);
  toggle.addEventListener('click',()=>{const open=panel.hidden;panel.hidden=!open;toggle.setAttribute('aria-expanded',String(open));toggle.classList.toggle('expanded',open);});
  const message={row,avatar,body,role,messageId,text,trace,phase,toggle,summary,panel,toolCount:0,searchCount:0,readCount:0,otherCount:0,lastTool:null,councilReviews:{}};addMessageActions(message);return message;
}
function renderAssistant(message,text){window.MathJax?.typesetClear?.([message.text]);message.text.innerHTML=safeMarkdown(text);typeset(message.text);scrollFeed();}
function updateResearchSummary(message,active=''){
  const pieces=[];
  if(message.searchCount)pieces.push(`Searched the web${message.searchCount>1?` ${message.searchCount} times`:''}`);
  if(message.readCount)pieces.push(`read ${message.readCount} ${message.readCount===1?'page':'pages'}`);
  if(message.otherCount)pieces.push(`${message.otherCount} more ${message.otherCount===1?'tool':'tools'}`);
  message.summary.textContent=active|| (pieces.length?pieces.join(', '):'Research activity');
}
function addResearchStatus(message,label){
  message.trace.hidden=false;message.phase.textContent=label||'Working on your question';
  message.trace.classList.add('has-activity');
}
function addResearchHistoryStatus(message,label){
  if(!label)return;
  message.trace.hidden=false;
  const line=document.createElement('div');line.className='research-history-status';line.textContent=label;
  message.panel.appendChild(line);
}
function groupResearchEvents(events){
  const grouped=new Map();let pending=[];
  for(const record of events||[]){
    const event=record.payload||record;
    if(event.type==='done'&&event.assistant_message_id){
      grouped.set(String(event.assistant_message_id),{events:pending,mode:event.mode||null});pending=[];
    }else pending.push(event);
  }
  return grouped;
}
function restoreResearchActivity(message,group){
  if(!group)return;
  const hasTools=group.events.some(event=>['tool_start','tool_result','council_reviewer','council_complete'].includes(event.type));
  if(group.mode==='chat'&&!hasTools)return;
  if(!group.mode&&!hasTools)return;
  for(const event of group.events){
    if(event.type==='status')addResearchHistoryStatus(message,event.label);
    else if(event.type==='tool_start')startResearchTool(message,event);
    else if(event.type==='tool_result')finishResearchTool(message,event);
    else if(event.type==='council_reviewer')handleCouncilEvent(message,event);
    else if(event.type==='council_complete')addResearchHistoryStatus(message,event.partial?'Council reviews were partial.':'Council reviews completed.');
    else if(event.type==='memory_saved'&&event.count)addResearchHistoryStatus(message,'Saved selected long-term context');
  }
  message.trace.classList.remove('research-working');message.phase.textContent='';message.lastTool?.details.classList.remove('is-active');
  message.lastTool=null;updateResearchSummary(message);
}
function councilModelLabel(model){
  if(model==='qwen/qwen3-next-80b-a3b-thinking')return 'Qwen 3 Next';
  if(model==='qwen/qwen3.5-122b-a10b')return 'Qwen 3.5';
  if(model==='nvidia/nemotron-3-ultra-550b-a55b')return 'Nemotron Ultra';
  if(model==='nvidia/nemotron-3.5-bolt-e4b')return 'Nemotron Bolt';
  return model||'NVIDIA reviewer';
}
function handleCouncilEvent(message,event){
  message.trace.hidden=false;message.phase.textContent='';
  const key=event.role||event.model;
  let item=message.councilReviews[key];
  if(!item){
    const details=document.createElement('details');details.className='research-tool council-review';
    const summary=document.createElement('summary');
    const kind=document.createElement('span');kind.className='research-tool-kind';kind.textContent='Council review';
    const title=document.createElement('span');title.className='research-tool-title';title.textContent=`${event.label||'Reviewer'} · ${councilModelLabel(event.model)}`;
    const arrow=document.createElement('span');arrow.className='research-chevron';arrow.textContent='›';summary.append(kind,title,arrow);
    const content=document.createElement('div');content.className='research-tool-content';content.textContent='Review in progress…';details.append(summary,content);message.panel.appendChild(details);
    item=message.councilReviews[key]={details,content,title};
  }
  if(event.status==='complete'){
    item.details.classList.remove('council-review-failed');item.title.textContent=`${event.label||'Reviewer'} · ${councilModelLabel(event.model)} · Complete`;
    item.content.innerHTML=safeMarkdown(event.summary||'The reviewer returned no summary.');typeset(item.content);
  }else if(event.status==='unavailable'||event.status==='timeout'||event.status==='failed'){
    item.details.classList.add('council-review-failed');
    const state=event.status==='unavailable'?'Unavailable':event.status==='timeout'?'Timed out':'Failed';
    item.title.textContent=`${event.label||'Reviewer'} · ${councilModelLabel(event.model)} · ${state}`;
    item.content.textContent=event.summary||'This reviewer did not complete.';
  }
  updateResearchSummary(message,event.status==='running'?'Council reviews running…':'Council review updated');
}
function researchToolLabel(tool,active=false){
  const labels={
    web_search:['Searching the web','Searched the web'],parallel_search:['Searching the web','Searched the web'],
    fetch_data:['Fetching a source','Fetched a source'],get_results:['Reading sources','Read sources'],
    symbolic_math:['Calculating','Calculated'],numerical_experiment:['Running a numerical experiment','Ran a numerical experiment'],
    create_pdf:['Creating a PDF','Created a PDF']
  };
  return (labels[tool]||['Using a tool','Used a tool'])[active?0:1];
}
function startResearchTool(message,event){
  if(event.tool==='web_search'||event.tool==='parallel_search')message.row.classList.add('has-web-search');
  message.trace.hidden=false;message.trace.classList.add('research-working');message.phase.textContent='';message.toolCount++;
  const details=document.createElement('details');details.className='research-tool is-active';
  const summary=document.createElement('summary');
  const kind=document.createElement('span');kind.className='research-tool-kind research-activity-text';kind.textContent=researchToolLabel(event.tool,true);
  const title=document.createElement('span');title.className='research-tool-title research-activity-text';title.textContent=event.label||'';
  const arrow=document.createElement('span');arrow.className='research-chevron';arrow.textContent='›';summary.append(kind,title,arrow);
  const content=document.createElement('div');content.className='research-tool-content';content.textContent='Working…';details.append(summary,content);message.panel.appendChild(details);
  message.lastTool={details,kind,content,tool:event.tool};
  updateResearchSummary(message,kind.textContent+'…');
}
function finishResearchTool(message,event){
  const summary=event.summary||{};const tool=event.tool;
  if((tool==='web_search'||tool==='parallel_search')&&event.ok)message.searchCount++;
  else if((tool==='fetch_data'||tool==='get_results')&&event.ok)message.readCount++;
  else message.otherCount++;
  const step=message.lastTool&&message.lastTool.tool===tool?message.lastTool:null;
  if(step){step.details.classList.remove('is-active');step.kind.classList.remove('research-activity-text');step.kind.textContent=researchToolLabel(tool);step.content.textContent='';step.content.classList.add('research-tool-result');message.trace.classList.remove('research-working');}
  const target=step?.content||message.panel;
  if(!event.ok){const error=document.createElement('p');error.className='research-error';error.textContent=summary.error||'The tool could not complete.';target.appendChild(error);}
  else if(tool==='web_search'||tool==='parallel_search'){
    const results=summary.results||[];
    if(!results.length){const empty=document.createElement('p');empty.textContent='The search returned no results.';target.appendChild(empty);}
    results.forEach(result=>{if(!result.url)return;const link=document.createElement('a');link.className='research-source-row';link.href=result.url;link.target='_blank';link.rel='noopener noreferrer';const title=document.createElement('span');title.className='research-source-title';title.textContent=result.title||result.url;const domain=document.createElement('span');domain.className='research-source-domain';try{domain.textContent=new URL(result.url).hostname.replace(/^www\./,'');}catch{domain.textContent=result.url;}link.append(title,domain);target.appendChild(link);});
  }else if(tool==='fetch_data'){
    const note=document.createElement('p');note.textContent=`Inspected ${summary.characters||0} characters from the fetched page.`;target.appendChild(note);
    if(summary.url){const link=document.createElement('a');link.className='research-source-row';link.href=summary.url;link.target='_blank';link.rel='noopener noreferrer';link.textContent=summary.title||summary.url;target.appendChild(link);}
  }else if(tool==='get_results'){
    (summary.pages||[]).forEach(page=>{const row=document.createElement('div');if(page.error){row.className='research-error';row.textContent=`${page.url}: ${page.error}`;}else{row.className='research-source-row';const title=document.createElement('span');title.className='research-source-title';title.textContent=page.title||page.url;const note=document.createElement('span');note.className='research-source-domain';note.textContent=`${(page.content||'').length} chars`;row.append(title,note);}if(!page.error){row.addEventListener('click',()=>window.open(page.url,'_blank','noopener'));row.setAttribute('role','link');}target.appendChild(row);});
  }else if(tool==='numerical_experiment'){
    const note=document.createElement('p');note.textContent=`${summary.valid_samples||0} valid points; ${summary.within_tolerance||0} within tolerance. Computational result, not a physical measurement.`;target.appendChild(note);
  }else if(tool==='create_pdf'&&summary.download_url){
    const link=document.createElement('a');link.className='research-source-row generated-file-link';link.href=summary.download_url;link.download=summary.filename||'claudex-document.pdf';link.textContent='Download '+(summary.filename||'PDF document');target.appendChild(link);
  }else{
    const note=document.createElement('p');note.textContent=summary.result||summary.operation||'Calculation completed.';target.appendChild(note);
  }
  updateResearchSummary(message);message.lastTool=null;
}

async function loadConfig(){
  const response=await fetch('/api/config');const config=await response.json();
  modelCatalog=config.models||{};selectedModel=config.model;selectedEffort=config.effort||defaultEffort(config.model);$('autoThink').checked=config.auto_think;
  $('effortValue').textContent=selectedEffort;setComposerModel(config.model);renderModelMenu();
  $('keyHelp').textContent=config.has_api_key?'A key is saved locally. Enter a new key only to replace it.':'Add your NVIDIA API key to use ClaudeX.';
  $('apiKey').placeholder=config.has_api_key?'Saved locally · enter a new key to replace':'nvapi-…';
  $('displayName').value=localStorage.getItem('claudex_display_name')||'';
  if(!config.has_api_key)showModal('settingsModal');
}
async function loadConversations(){
  const response=await fetch('/api/conversations',{cache:'no-store'});const conversations=await response.json();const list=$('conversationList');list.innerHTML='';
  // Defend against stale servers or cached API payloads that still contain
  // abandoned placeholder rows from an older ClaudeX build.
  const visibleConversations=conversations.filter(conversation=>(conversation.preview||'').trim().length>0);
  visibleConversations.forEach(conversation=>list.appendChild(createConversationRow(conversation)));
}
function createConversationRow(conversation){
  const row=document.createElement('div');row.className='conversation-item'+(conversation.id===activeConversation?' selected':'');
  const open=document.createElement('button');open.type='button';open.className='conv-open';open.title=conversation.title||'';open.onclick=()=>openConversation(conversation.id);
  const title=document.createElement('span');title.className='conv-title';title.textContent=conversation.title||'Chat';open.appendChild(title);
  const menuButton=document.createElement('button');menuButton.type='button';menuButton.className='conv-menu-toggle';menuButton.title='Chat actions';menuButton.setAttribute('aria-label',`Actions for ${conversation.title||'chat'}`);menuButton.setAttribute('aria-haspopup','menu');menuButton.setAttribute('aria-expanded','false');
  menuButton.innerHTML='<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="3" r="1"/><circle cx="8" cy="8" r="1"/><circle cx="8" cy="13" r="1"/></svg>';
  const menu=document.createElement('div');menu.className='conv-menu';menu.setAttribute('role','menu');menu.hidden=true;
  const rename=document.createElement('button');rename.type='button';rename.className='conv-menu-action';rename.setAttribute('role','menuitem');rename.textContent='Edit name';
  rename.onclick=event=>{event.stopPropagation();menu.hidden=true;menuButton.setAttribute('aria-expanded','false');renameConversation(conversation);};
  const remove=document.createElement('button');remove.type='button';remove.className='conv-menu-action danger';remove.setAttribute('role','menuitem');remove.textContent='Delete';
  remove.onclick=event=>{event.stopPropagation();menu.hidden=true;menuButton.setAttribute('aria-expanded','false');deleteConversation(conversation);};menu.append(rename,remove);
  menuButton.onclick=event=>{event.stopPropagation();const opening=menu.hidden;document.querySelectorAll('.conv-menu').forEach(other=>{other.hidden=true;other.classList.remove('opens-above');});document.querySelectorAll('.conv-menu-toggle').forEach(other=>other.setAttribute('aria-expanded','false'));if(opening&&window.innerHeight-menuButton.getBoundingClientRect().bottom<85)menu.classList.add('opens-above');menu.hidden=!opening;menuButton.setAttribute('aria-expanded',String(opening));};
  row.append(open,menuButton,menu);return row;
}
async function renameConversation(conversation){const title=window.prompt('Edit chat name',conversation.title||'Chat');if(title===null)return;const clean=title.trim();if(!clean){toast('Chat name cannot be empty.',true);return;}if(Array.from(clean).length>120){toast('Chat names can contain up to 120 characters.',true);return;}try{const response=await fetch(`/api/conversations/${encodeURIComponent(conversation.id)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:clean})});const data=await response.json();if(!response.ok)throw new Error(data.detail||'Could not rename this chat.');if(activeConversation===conversation.id)$('workspaceTitle').textContent=data.title;await loadConversations();}catch(error){toast(error.message,true);}}
async function deleteConversation(conversation){if(!await confirmDeleteChat(conversation.title))return;try{const response=await fetch(`/api/conversations/${encodeURIComponent(conversation.id)}`,{method:'DELETE'});const data=await response.json();if(!response.ok)throw new Error(data.detail||'Could not delete this chat.');if(activeConversation===conversation.id){activeConversation=null;pendingAttachments=[];renderPendingAttachments();sessionStorage.removeItem('claudex_active_conversation');showWelcome();}await loadConversations();toast('Chat permanently deleted.');}catch(error){toast(error.message,true);}}
function startNewChat(){if(isSending)return;activeConversation=null;pendingAttachments=[];renderPendingAttachments();sessionStorage.removeItem('claudex_active_conversation');showWelcome();loadConversations();$('promptInput').focus();}
async function openConversation(id){
  const response=await fetch('/api/conversations/'+id);if(!response.ok){sessionStorage.removeItem('claudex_active_conversation');activeConversation=null;showWelcome();return;}
  const data=await response.json();activeConversation=id;pendingAttachments=data.pending_attachments||[];renderPendingAttachments();sessionStorage.setItem('claudex_active_conversation',id);$('workspaceTitle').textContent=data.conversation.title||'Chat';$('messageFeed').innerHTML='';
  if(!data.messages.length){showWelcome();$('workspaceTitle').textContent=data.conversation.title||'New chat';}
  const activityByAssistant=groupResearchEvents(data.events);
  data.messages.forEach(saved=>{const message=createMessage(saved.role,saved.content,saved.attachments||[],saved.id);if(saved.role==='assistant')restoreResearchActivity(message,activityByAssistant.get(String(saved.id)));});await loadConversations();
}
async function ensureConversation(){if(activeConversation)return activeConversation;const response=await fetch('/api/conversations',{method:'POST'});const data=await response.json();activeConversation=data.id;sessionStorage.setItem('claudex_active_conversation',data.id);return data.id;}
async function sendMessage(options={}){
  if(isSending||isUploadingFiles)return;const typedPrompt=options.editedText??$('promptInput').value;const hasFiles=!options.messageId&&pendingAttachments.length>0;if(!typedPrompt.trim()&&!hasFiles)return;
  const sendingAttachments=options.messageId?[]:[...pendingAttachments];
  const prompt=typedPrompt||'Please read the attached file(s), explain what they contain, and note anything important.';
  // Lock before any network await so rapid clicks cannot create duplicate conversations.
  isSending=true;$('sendBtn').disabled=true;$('promptInput').disabled=true;
  let prepared=false;
  try{
    const config=await(await fetch('/api/config')).json();
    if(!config.has_api_key){showModal('settingsModal');toast('Add your NVIDIA API key first.',true);}
    else{await ensureConversation();prepared=true;}
  }catch(error){toast('Could not start this chat: '+String(error.message||error),true);}
  if(!prepared){isSending=false;$('promptInput').disabled=false;updateSendState();return;}
  if(!options.messageId){$('promptInput').value='';autoResize();}
  const userMessage=options.userRow||createMessage('user',prompt,sendingAttachments);userMessage.text.textContent=prompt;
  if(options.messageId){let next=userMessage.row.nextElementSibling;while(next){const following=next.nextElementSibling;next.remove();next=following;}}
  const assistant=createMessage('assistant','');assistant.row.classList.toggle('chat-mode',!researchMode&&!councilMode);assistant.row.classList.add('processing');assistant.avatar.classList.add('processing');assistant.actions.hidden=true;currentAssistantText='';
  try{
    const response=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({conversation_id:activeConversation,message:prompt,message_id:options.messageId||null,attachment_ids:sendingAttachments.map(file=>file.id),auto_think:researchMode||councilMode||$('autoThink').checked,council_mode:councilMode,mode:councilMode?'council':researchMode?'research':'chat',auto_memory:true})});
    if(!response.ok){const error=await response.json();throw new Error(error.detail||'Request failed');}
    pendingAttachments=pendingAttachments.filter(file=>!sendingAttachments.some(sent=>sent.id===file.id));renderPendingAttachments();
    const reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';
    while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});const blocks=buffer.split(/\r?\n\r?\n/);buffer=blocks.pop();
      for(const block of blocks){const line=block.split(/\r?\n/).find(part=>part.startsWith('data: '));if(!line)continue;const event=JSON.parse(line.slice(6));
        if(event.type==='status')addResearchStatus(assistant,event.label);
        else if(event.type==='council_reviewer')handleCouncilEvent(assistant,event);
        else if(event.type==='council_complete')addResearchStatus(assistant,event.partial?`Partial Council: ${event.completed} of ${event.total} reviews completed.`:'Council reviews complete; checking their claims.');
        else if(event.type==='tool_start')startResearchTool(assistant,event);
        else if(event.type==='tool_result')finishResearchTool(assistant,event);
        else if(event.type==='token'){currentAssistantText+=event.text;renderAssistant(assistant,currentAssistantText);}
        else if(event.type==='memory_saved'&&event.count)addResearchStatus(assistant,'Saved selected long-term context');
        else if(event.type==='error'){if(event.user_message_id)userMessage.messageId=event.user_message_id;throw new Error(event.message);}
        else if(event.type==='done'){userMessage.messageId=event.user_message_id||userMessage.messageId;}
      }
    }
    assistant.row.classList.remove('processing');assistant.avatar.classList.remove('processing');assistant.actions.hidden=false;if(!currentAssistantText)assistant.text.textContent='No response text was received.';await loadConversations();
  }catch(error){assistant.row.classList.remove('processing');assistant.avatar.classList.remove('processing');assistant.actions.hidden=false;assistant.trace.classList.remove('research-working');assistant.lastTool?.details.classList.remove('is-active');const message='**ClaudeX encountered an error**\n\n'+String(error.message||error);currentAssistantText+=(currentAssistantText?'\n\n':'')+message;renderAssistant(assistant,currentAssistantText);addResearchStatus(assistant,'Request failed: '+String(error.message||error));toast('ClaudeX request failed.',true);}
  finally{isSending=false;$('promptInput').disabled=false;updateSendState();$('promptInput').focus();await loadConversations();}
}
async function saveSettings(){
  const payload={api_key:$('apiKey').value.trim()||null,model:selectedModel,effort:selectedEffort,auto_think:$('autoThink').checked,auto_memory:true};$('connectionStatus').textContent='Saving…';
  try{const response=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw new Error(data.detail||'Could not save settings');$('apiKey').value='';$('autoThink').checked=payload.auto_think;$('connectionStatus').textContent='Settings saved.';toast('Settings saved.');await loadConfig();}
  catch(error){$('connectionStatus').textContent=error.message;toast(error.message,true);}
}
async function setSelectedModel(model){
  const effort=defaultEffort(model);const response=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:null,model,effort,auto_think:$('autoThink').checked,auto_memory:true})});
  if(!response.ok){toast('Could not change model.',true);await loadConfig();return;}selectedEffort=effort;$('effortValue').textContent=selectedEffort;setComposerModel(model);renderModelMenu();closePickers();
}
async function setSelectedEffort(effort){
  const response=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:null,model:selectedModel,effort,auto_think:$('autoThink').checked,auto_memory:true})});
  if(!response.ok){toast('Could not change reasoning effort.',true);await loadConfig();return;}selectedEffort=effort;$('effortValue').textContent=effort;setComposerModel(selectedModel);renderEffortMenu();closePickers();
}
function renderEffortMenu(){
  const menu=$('effortMenu');menu.innerHTML='';const explanation=document.createElement('div');explanation.className='effort-explainer';explanation.textContent=effortExplanation(selectedModel);menu.appendChild(explanation);
  (modelCatalog[selectedModel]?.efforts||[]).forEach(effort=>{const button=document.createElement('button');button.type='button';button.className='effort-option'+(effort===selectedEffort?' selected':'');const label=document.createElement('span');label.textContent=effort;const check=document.createElement('span');check.textContent=effort===selectedEffort?'✓':'';button.append(label,check);button.addEventListener('click',()=>setSelectedEffort(effort));menu.appendChild(button);});
}
async function loadMemories(){
  const response=await fetch('/api/memories');const memories=await response.json();const list=$('memoryList');list.innerHTML='';
  if(!memories.length){list.innerHTML='<div class="memory-empty">No saved context yet.</div>';return;}
  memories.forEach(memory=>{const row=document.createElement('div');row.className='memory-item';const copy=document.createElement('div');copy.className='memory-copy';const category=document.createElement('small');category.textContent=memory.category;const content=document.createElement('div');content.textContent=memory.content;copy.append(category,content);const remove=document.createElement('button');remove.textContent='×';remove.title='Remove saved memory';remove.onclick=async()=>{await fetch('/api/memories/'+memory.id,{method:'DELETE'});await loadMemories();};row.append(copy,remove);list.appendChild(row);});
}
function setMode(mode){researchMode=mode==='research';councilMode=mode==='council';$('chatMode').classList.toggle('selected',mode==='chat');$('researchMode').classList.toggle('selected',researchMode);$('councilMode').classList.toggle('selected',councilMode);$('councilMode').setAttribute('aria-pressed',String(councilMode));$('autoThink').checked=researchMode||councilMode;}

function setupMobileConnectionSettings(){
  if(!window.__CLAUDEX_REMOTE_MODE__)return;
  const section=$('mobileConnectionSettings');section.hidden=false;
  $('remoteServerUrl').value=localStorage.getItem('claudex_remote_url')||'';
  $('remoteAccessToken').value=localStorage.getItem('claudex_remote_token')||'';
  $('saveMobileConnection').onclick=()=>{
    try{
      const url=new URL($('remoteServerUrl').value.trim());
      const token=$('remoteAccessToken').value.trim();
      if((url.protocol!=='https:'&&url.hostname!=='localhost')||url.pathname!=='/'||url.search||url.hash||url.username||url.password)throw new Error('Use a secure HTTPS server origin without a path.');
      if(token.length<32)throw new Error('The access token must contain at least 32 characters.');
      localStorage.setItem('claudex_remote_url',url.origin);localStorage.setItem('claudex_remote_token',token);location.reload();
    }catch(error){toast(error.message,true);}
  };
}
window.addEventListener('claudex-remote-ready',setupMobileConnectionSettings);
if(window.__CLAUDEX_REMOTE_MODE__)setupMobileConnectionSettings();

$('sendBtn').addEventListener('click',sendMessage);
$('promptInput').addEventListener('input',()=>{autoResize();updateSendState();});
$('promptInput').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMessage();}});
$('newChatWide').addEventListener('click',startNewChat);$('homeButton').addEventListener('click',startNewChat);$('refreshHistory').addEventListener('click',loadConversations);$('historyNav').addEventListener('click',()=>{});
$('chatMode').addEventListener('click',()=>setMode('chat'));$('researchMode').addEventListener('click',()=>setMode('research'));$('councilMode').addEventListener('click',()=>setMode(councilMode?'chat':'council'));$('autoThink').addEventListener('change',()=>setMode($('autoThink').checked?'research':'chat'));
$('settingsNav').addEventListener('click',()=>showModal('settingsModal'));$('saveSettings').addEventListener('click',saveSettings);$('testConnection').addEventListener('click',async()=>{if($('apiKey').value.trim())await saveSettings();$('connectionStatus').textContent='Checking NVIDIA connection…';try{const response=await fetch('/api/test-key',{method:'POST'});const data=await response.json();if(!response.ok)throw new Error(data.detail||'Connection failed');$('connectionStatus').textContent='NVIDIA connection is working.';toast('Connection successful.');}catch(error){$('connectionStatus').textContent=error.message;toast(error.message,true);}});
$('showKey').addEventListener('click',()=>{const field=$('apiKey');field.type=field.type==='password'?'text':'password';$('showKey').textContent=field.type==='password'?'Show':'Hide';});
$('displayName').addEventListener('change',()=>localStorage.setItem('claudex_display_name',$('displayName').value.trim().slice(0,48)));$('saveDisplayName').addEventListener('click',()=>{localStorage.setItem('claudex_display_name',$('displayName').value.trim().slice(0,48));toast('Greeting updated.');if(!$('messageFeed').querySelector('.message-row'))showWelcome();});
$('clearMemories').addEventListener('click',async()=>{if(!confirm('Clear all saved context?'))return;await fetch('/api/memories',{method:'DELETE'});await loadMemories();toast('Saved context cleared.');});
$('modelPicker').addEventListener('click',()=>{const open=$('modelMenu').hidden;closePickers();if(open){renderModelMenu();$('modelMenu').hidden=false;$('modelPicker').setAttribute('aria-expanded','true');}});
$('effortPicker').addEventListener('click',()=>{const open=$('effortMenu').hidden;$('effortMenu').innerHTML='';renderEffortMenu();$('effortMenu').hidden=!open;$('effortPicker').setAttribute('aria-expanded',String(open));});
document.addEventListener('click',event=>{if(!event.target.closest('.model-control'))closePickers();});
document.addEventListener('click',event=>{if(!event.target.closest('.conversation-item')){document.querySelectorAll('.conv-menu').forEach(menu=>menu.hidden=true);document.querySelectorAll('.conv-menu-toggle').forEach(button=>button.setAttribute('aria-expanded','false'));}});
$('addFiles').addEventListener('click',()=>{const open=$('addFilesMenu').hidden;$('addFilesMenu').hidden=!open;$('addFiles').setAttribute('aria-expanded',String(open));});
$('uploadFiles').addEventListener('click',()=>{$('addFilesMenu').hidden=true;$('addFiles').setAttribute('aria-expanded','false');$('fileInput').click();});
$('fileInput').addEventListener('change',event=>{uploadSelectedFiles(event.target.files);event.target.value='';});
document.addEventListener('click',event=>{if(!event.target.closest('.add-files-control')){$('addFilesMenu').hidden=true;$('addFiles').setAttribute('aria-expanded','false');}});
document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>hideModal(button.dataset.close)));
document.querySelectorAll('.modal-backdrop').forEach(modal=>modal.addEventListener('click',event=>{if(event.target!==modal)return;if(modal.id==='deleteConfirmModal')settleDeleteChatConfirmation(false);else hideModal(modal.id);}));
$('cancelDeleteChat').addEventListener('click',()=>settleDeleteChatConfirmation(false));$('confirmDeleteChat').addEventListener('click',()=>settleDeleteChatConfirmation(true));
document.addEventListener('click',event=>{const chip=event.target.closest('[data-prompt]');if(chip){$('promptInput').value=chip.dataset.prompt;autoResize();$('promptInput').focus();}});
$('openSidebar').addEventListener('click',()=>$('sidebar').classList.add('open'));$('closeSidebar').addEventListener('click',()=>$('sidebar').classList.remove('open'));document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='u'){event.preventDefault();$('fileInput').click();}if(event.key==='Escape'){if(!$('deleteConfirmModal').hidden&&$('deleteConfirmModal').classList.contains('open'))settleDeleteChatConfirmation(false);$('sidebar').classList.remove('open');closePickers();$('addFilesMenu').hidden=true;$('addFiles').setAttribute('aria-expanded','false');}});

(async()=>{try{await loadConfig();updateSendState();await loadConversations();const saved=sessionStorage.getItem('claudex_active_conversation');if(saved)await openConversation(saved);else showWelcome();}catch(error){toast('Could not initialize ClaudeX: '+error.message,true);}})();
