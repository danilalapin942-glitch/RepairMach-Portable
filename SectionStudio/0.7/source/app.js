'use strict';
(() => {
const C=SectionCore,$=id=>document.getElementById(id),COL=['#117a91','#b76a22'],LIGHT=['#e8f3f6','#fcf0e3'],EDIT=[0,1,2,3,4,5,19,20,21,22,23];
const T=SectionTemplates;let appTab='editor',templateId=T.data.templates[0]?.id||null;
let project=C.demo(),cursor=12,active=0,history=[],dirty=false,meshes=[],frame=0,dragPoint=null,dragCamera=null,sectionMap=null,handles=[],planeMaps={},sectionZoom=1,gap={state:'unavailable'},camera={a:-.88,e:.48,zoom:1},vspDraft=null;
let inputDraft=null,auditReport=null,auditEpoch=0,auditRevision=0,auditFullTimer=0,auditLocalTimer=0,auditLocalWorker=null,auditFullWorker=null,auditLocalBusy=false,auditLocalPending=false,auditLastKey='',auditLastXML=null,auditCachedMeta={},auditUpdating=false,auditFallback=false,loadRequest=0;
const fmt=n=>Number(n.toFixed(5)).toString();
const numeric=raw=>raw.trim()?Number(raw.trim().replace(',','.')):NaN;
function notice(t,error=false){$('status').textContent=t;$('status').classList.toggle('error',error);}
function push(){history.push({project:C.clone(project),cursor,active});if(history.length>60)history.shift();dirty=true;invalidateAudit();}
function ranges(){const ss=project.bodies.filter(b=>b.enabled).flatMap(b=>b.sections);return {min:Math.min(...ss.map(s=>s.x)),max:Math.max(...ss.map(s=>s.x))};}
function exact(i){return C.sectionAt(project.bodies[i],cursor);}
function displayMeshes(){return meshes.flatMap((mesh,i)=>!mesh.length?[]:[{mesh,body:i},...(project.bodies[i].mirrorY?[{mesh:mesh.map(row=>row.map(p=>[p[0],-p[1],p[2]])),body:i,mirror:true}]:[])]);}
function openError(message){notice(message,true);$('loadErrorText').textContent=message;if(!$('loadErrorDialog').open)$('loadErrorDialog').showModal();}
function installProject(q){
 const preview=q.bodies.map(b=>C.loft(b));
 inputDraft=null;dragPoint=null;push();project=q;meshes=preview;active=0;
 const common=q.bodies[0].sections.find(s=>s.width>0&&q.bodies[1].sections.some(r=>Math.abs(r.x-s.x)<1e-8&&r.width>0));
 cursor=common?.x??q.bodies[0].sections[Math.min(2,q.bodies[0].sections.length-1)].x;dirty=false;sectionZoom=1;sync();
}
function bodyCard(i){
 const label=i?'Мотогондола':'Фюзеляж';
 return `<div class="panel-head"><div class="body-head"><i class="dot ${i?'g':''}"></i><span class="body-title">${label}</span></div>${i?'<label class="small"><input id="enabled1" type="checkbox"> Включена</label>':'<span class="small">Общие X / Y / Z</span>'}</div>
 <div class="body-state" id="state${i}"></div><div class="name-row"><label for="name${i}">Имя</label><input id="name${i}" maxlength="40"><button id="add${i}">Сечение здесь</button><button id="remove${i}">Удалить</button></div>
 <div class="fields">${[['x','X сечения, ft'],['y','Центр Y, ft'],['z','Центр Z, ft'],['width','Ширина, ft'],['height','Высота, ft']].map(([k,l])=>`<div class="field"><label for="${k}${i}">${l}</label><input id="${k}${i}" inputmode="decimal"></div>`).join('')}<div class="field"><label for="shape${i}">Форма сечения</label><select id="shape${i}"><option value="ellipse">Эллипс</option><option value="chine">Гранёный корпус</option><option value="trapezoid">Трапеция</option><option value="point">Точка на торце</option><option value="custom" disabled>Своя форма</option></select></div></div>
 <button id="library${i}" class="template-shortcut">Выбрать из библиотеки сечений</button><div class="source-skin hidden" id="sourceSkinRow${i}"><label><input id="sourceSkin${i}" type="checkbox"> Сохранять сглаживание из VSP3</label></div><div class="smooth"><label for="strength${i}">Сглаживание OpenVSP</label><input id="strength${i}" type="range" min="0" max="1" step=".05"><strong id="strengthValue${i}"></strong></div>
 <div class="shift-row"><div><label for="dx${i}">Сдвиг всего тела ΔX, ft</label><input id="dx${i}" inputmode="decimal" value="0"></div><div><label for="dz${i}">Сдвиг всего тела ΔZ, ft</label><input id="dz${i}" inputmode="decimal" value="0"></div><button id="shift${i}">Сдвинуть</button></div>`;
}
for(let i=0;i<2;i++)$('body'+i).innerHTML=bodyCard(i);
function commit(q,after){const e=C.validate(q);if(e.length){sync();notice(e[0],true);return false;}push();project=q;if(after)after();sync();return true;}
function setCursor(x){const r=ranges();if(!Number.isFinite(x)||x<r.min-1e-8||x>r.max+1e-8){sync();notice(`Плоскость X должна быть в пределах ${fmt(r.min)}…${fmt(r.max)} ft.`,true);return;}cursor=Math.max(r.min,Math.min(r.max,x));sectionZoom=1;sync(false);}
function sync(rebuild=true){
 const imported=project.source?.kind==='vsp3';$('sourceBar').classList.toggle('hidden',!imported);$('sourceLabel').textContent=imported?`${project.source.name} · ${project.source.units} в исходном файле → ft в редакторе` :'';$('exportBtn').textContent=imported?'Сохранить VSP3':'Экспорт в OpenVSP';$('modelOrigin').textContent=imported?'Выбранные тела · предварительная поверхность':'Учебная форма · не геометрия самолёта';
 const r=ranges();cursor=Math.max(r.min,Math.min(r.max,cursor));$('modelName').value=project.name;$('cursorX').value=fmt(cursor);$('cursorRange').min=r.min;$('cursorRange').max=r.max;$('cursorRange').value=cursor;$('rangeMin').textContent=fmt(r.min);$('rangeMax').textContent=fmt(r.max);$('sectionTitle').textContent=`Сечение · X = ${fmt(cursor)} ft`;
 const stations=[...new Set(project.bodies.filter(b=>b.enabled).flatMap(b=>b.sections.map(s=>s.x)))].sort((a,b)=>a-b);
 $('stationCount').textContent=stations.length;$('stationList').replaceChildren();
 for(const x of stations){const bt=document.createElement('button');bt.dataset.x=x;bt.className='station'+(Math.abs(x-cursor)<1e-8?' active':'');bt.innerHTML=`<strong>${fmt(x)} ft</strong><span class="dots">${project.bodies.map((b,i)=>`<i class="dot ${i?'g':''} ${b.enabled&&b.sections.some(s=>Math.abs(s.x-x)<1e-8)?'':'off'}"></i>`).join('')}</span>`;bt.onclick=()=>setCursor(x);$('stationList').append(bt);}
 $('stats').textContent=`Фюзеляж: ${project.bodies[0].sections.length} сеч. · гондола: ${project.bodies[1].enabled?project.bodies[1].sections.length:'выключена'}\nОбщая длина: ${fmt(r.max-r.min)} ft`;
 $('undoBtn').disabled=!history.length;$('enabled1').checked=project.bodies[1].enabled;
 $('addBothBtn').disabled=!project.bodies.some((b,i)=>{const a=exact(i);return b.enabled&&a&&a.index<0&&b.sections.length<64;});
 for(let i=0;i<2;i++){
  const b=project.bodies[i],a=exact(i),s=a?.section,edit=!!a&&a.index>=0&&b.enabled;
  $('body'+i).classList.toggle('active',i===active);$('name'+i).value=b.name;
  $('state'+i).textContent=!b.enabled?(imported?'Скрыта. В исходной модели будет сохранена без правок.':'Компонент выключен: скрыт и не экспортируется.'):!a?'В этой плоскости компонент отсутствует.':edit?`Заданное сечение ${a.index+1} из ${b.sections.length} · можно редактировать`:'Промежуточный контур · добавь сечение для редактирования';
  if(b.enabled&&a){if(s.freeform)$('state'+i).textContent+=' · '+(s.symmetric?'симметричный свободный контур':'свободный контур, обе стороны редактируются');if(b.mirrorY)$('state'+i).textContent+=' · зеркальная копия по Y';if(s.roll||s.pitch||s.yaw)$('state'+i).textContent+=` · повороты X/Y/Z: ${fmt(s.roll||0)} / ${fmt(s.pitch||0)} / ${fmt(s.yaw||0)}°`;if(b.endClosures?.length)$('state'+i).textContent+=' · торцевая станция сохранена отдельно';}
  if(s?.template)$('state'+i).textContent+=' · на основе: '+s.template.name;
  $('library'+i).disabled=!b.enabled;
  for(const k of ['x','y','z','width','height']){const el=$(k+i);if(inputDraft?.element!==el)el.value=s?fmt(s[k]??0):'';el.disabled=!edit||((k==='width'||k==='height')&&s?.shape==='point');}
  $('shape'+i).value=s?.shape||'ellipse';$('shape'+i).disabled=!edit;
  $('add'+i).disabled=!a||edit||b.sections.length>=64;$('remove'+i).disabled=!edit||b.sections.length<=3||a.index===0||a.index===b.sections.length-1;
  $('sourceSkinRow'+i).classList.toggle('hidden',!imported);$('sourceSkin'+i).checked=b.preserveSkin!==false;
  $('strength'+i).value=b.strength;$('strengthValue'+i).textContent=imported&&b.preserveSkin!==false?'из VSP3':b.strength.toFixed(2);$('strength'+i).disabled=!b.enabled||(imported&&b.preserveSkin!==false);
  for(const k of ['dx','dz','shift'])$(k+i).disabled=!b.enabled;
 }
 const e=C.validate(project,false);notice(e.length?e[0]:`Оба тела используют общие X / Y / Z, размеры — в футах. ${dirty?'Есть несохранённые изменения.':'Проект готов к редактированию.'}`,!!e.length);
 if(rebuild){meshes=project.bodies.map(b=>inputDraft?C.loft(b,32,32):C.loft(b));changedAuditGeometry();}paintStationRisks();if(appTab==='library')renderTemplateTargets();schedule();
}
for(let i=0;i<2;i++){
 $('body'+i).addEventListener('pointerdown',()=>{active=i;for(let j=0;j<2;j++)$('body'+j).classList.toggle('active',i===j);});
 for(const k of ['x','y','z','width','height']){
  const el=$(k+i);
  el.addEventListener('focus',()=>{const a=exact(i);if(a&&a.index>=0)inputDraft={element:el,body:i,index:a.index,key:k,changed:false};});
  el.addEventListener('input',()=>{const d=inputDraft;if(!d||d.element!==el)return;const q=C.clone(project),v=numeric(el.value);q.bodies[i].sections[d.index][k]=v;const error=C.validate(q,false);el.classList.toggle('invalid',!!error.length);if(error.length){notice(error[0],true);return;}if(!d.changed){push();d.changed=true;}project=q;active=i;if(k==='x')cursor=v;sync();});
  const finish=()=>{if(inputDraft?.element!==el)return;inputDraft=null;el.classList.remove('invalid');sync();};
  el.addEventListener('change',finish);el.addEventListener('blur',finish);
 }
 $('name'+i).addEventListener('change',()=>{const q=C.clone(project);q.bodies[i].name=$('name'+i).value.trim();commit(q);});
 $('shape'+i).addEventListener('change',()=>{
  const a=exact(i);if(!a||a.index<0)return;const kind=$('shape'+i).value,q=C.clone(project),b=q.bodies[i],s=b.sections[a.index];
  if(kind==='point'&&a.index!==0&&a.index!==b.sections.length-1){sync();notice('Точка допустима только на торце.',true);return;}
  s.shape=kind;s.points=C.preset(kind);delete s.knots;delete s.freeform;delete s.symmetric;delete s.template;if(kind==='point'){s.width=s.height=0;}else if(s.width===0){const other=b.sections[a.index===0?1:a.index-1];s.width=Math.max(.1,other.width*.3);s.height=Math.max(.1,other.height*.3);}commit(q);
 });
 $('strength'+i).addEventListener('input',()=>$('strengthValue'+i).textContent=Number($('strength'+i).value).toFixed(2));
 $('strength'+i).addEventListener('change',()=>{const q=C.clone(project);q.bodies[i].strength=Number($('strength'+i).value);commit(q);});
 $('sourceSkin'+i).onchange=()=>{const q=C.clone(project);q.bodies[i].preserveSkin=$('sourceSkin'+i).checked;commit(q);};
 $('add'+i).onclick=()=>{try{const q=C.clone(project);C.addAt(q.bodies[i],cursor);commit(q);}catch(e){notice(e.message,true);}};
 $('remove'+i).onclick=()=>{const a=exact(i),b=project.bodies[i];if(!a||a.index<=0||a.index>=b.sections.length-1||b.sections.length<=3)return;const q=C.clone(project);q.bodies[i].sections.splice(a.index,1);commit(q);};
 $('shift'+i).onclick=()=>{const dx=numeric($('dx'+i).value),dz=numeric($('dz'+i).value);if(!Number.isFinite(dx)||!Number.isFinite(dz)){notice('Введи числовые сдвиги ΔX и ΔZ.',true);return;}if(dx===0&&dz===0)return;const q=C.clone(project);q.bodies[i].sections.forEach(s=>{s.x+=dx;s.z+=dz;});if(commit(q)){$('dx'+i).value=$('dz'+i).value='0';}};
 $('library'+i).onclick=()=>{active=i;$('templateRole').value=i?'gondola':'fuselage';setAppTab('library');renderTemplateLibrary();};
}
function templateSVG(t,large=false){
 const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 220 150');svg.setAttribute('aria-hidden','true');
 const scale=Math.min(184/t.widthFt,114/t.heightFt),point=p=>[110+p[0]*t.widthFt*scale,75-p[1]*t.heightFt*scale],p=t.points.map(point);
 const axes=document.createElementNS(ns,'path');axes.setAttribute('d','M110 12V138M12 75H208');axes.setAttribute('stroke','#d9e6ed');axes.setAttribute('stroke-width','1');svg.append(axes);
 const path=document.createElementNS(ns,'path');let d='M'+p[0].join(' ');for(let i=1;i<p.length;i+=3)d+=' C'+p.slice(i,i+3).map(q=>q.join(' ')).join(' ');path.setAttribute('d',d+' Z');path.setAttribute('fill',t.role==='gondola'?'#fcf0e3':'#e8f3f6');path.setAttribute('stroke',t.role==='gondola'?COL[1]:COL[0]);path.setAttribute('stroke-width',large?'2.2':'1.8');svg.append(path);return svg;
}
function setAppTab(tab){
 appTab=tab;
 for(const [key,id] of [['editor','editor'],['library','template']]){const selected=key===tab;$(id+'Pane').classList.toggle('hidden',!selected);$(id+'Tab').setAttribute('aria-selected',String(selected));$(id+'Tab').tabIndex=selected?0:-1;}
 if(tab==='library'){$('templateTarget').value=String(active);renderTemplateTargets(true);renderTemplateLibrary();}else schedule();
}
function renderTemplateTargets(reset=false){
 const target=$('templateTarget'),old=$('templateStation').value;let body=Number(target.value);if(!project.bodies[body]?.enabled)body=0;target.value=String(body);
 for(let i=0;i<2;i++){target.options[i].textContent=(i?'Мотогондола':'Фюзеляж')+' · '+project.bodies[i].name;target.options[i].disabled=!project.bodies[i].enabled;}
 const b=project.bodies[body],select=$('templateStation');select.replaceChildren();b.sections.forEach((s,i)=>{const o=document.createElement('option');o.value=i;o.textContent=`Сечение ${i+1} · X=${fmt(s.x)} ft`+(s.shape==='point'?' · точка':'');select.append(o);});
 const nearest=b.sections.reduce((best,s,i)=>s.width>0&&(best<0||Math.abs(s.x-cursor)<Math.abs(b.sections[best].x-cursor))?i:best,-1);
 select.value=!reset&&old!==''&&b.sections[Number(old)]?old:String(nearest<0?0:nearest);updateTemplateApplyState();
}
function updateTemplateApplyState(){
 const t=T.data.templates.find(t=>t.id===templateId),b=project.bodies[Number($('templateTarget').value)],s=b?.sections[Number($('templateStation').value)],point=s?.shape==='point';
 $('templateApply').disabled=!t||!b?.enabled||!s||point&&$('templateSizeMode').value==='fit';
 $('templateApplyNote').textContent=point&&$('templateSizeMode').value==='fit'?'Для точечной станции выбери исходные размеры шаблона.':s?`Будет заменена одна станция при X=${fmt(s.x)} ft. Правку можно отменить.`:'Выбери станцию текущей модели.';
}
function renderTemplateDetail(){
 const t=T.data.templates.find(t=>t.id===templateId);$('templatePreview').replaceChildren();
 if(!t){$('templateName').textContent='Сечения не найдены';$('templateMeta').textContent='Измени поиск или фильтры.';updateTemplateApplyState();return;}
 $('templateName').textContent=t.name;$('templatePreview').append(templateSVG(t,true));
 $('templateMeta').textContent=`Ширина × высота: ${fmt(t.widthFt)} × ${fmt(t.heightFt)} ft\nИсходная станция X: ${fmt(t.xFt)} ft · ${fmt(t.stationFraction*100)}% длины\n${t.symmetric?'Симметричный':'Асимметричный'} контур · ${(t.points.length-1)/3} кубических сегм.\nИсточник: ${t.source.filename}`;updateTemplateApplyState();
}
function renderTemplateLibrary(){
 const normalize=s=>s.toLocaleLowerCase().replace(/ё/g,'е').replace(/[_\-\u2010-\u2015]/g,''),query=$('templateSearch').value.trim(),model=$('templateModel').value,role=$('templateRole').value;
 const tokens=query.split(/\s+/).filter(Boolean).map(normalize),items=T.data.templates.filter(t=>(!model||t.model===model)&&(!role||t.role===role)&&tokens.every(word=>normalize(t.name+' '+t.component).includes(word)));
 if(!items.some(t=>t.id===templateId))templateId=items[0]?.id||null;
 $('templateResults').textContent=`Показано ${items.length} из ${T.data.templates.length} сечений`;$('templateGrid').replaceChildren();
 if(!items.length){const empty=document.createElement('div');empty.className='template-empty';empty.textContent='Под этот поиск сечений нет. Сбрось фильтры или введи название самолёта.';$('templateGrid').append(empty);}
 for(const t of items){const card=document.createElement('button');card.className='template-card';card.dataset.template=t.id;card.setAttribute('aria-pressed',String(templateId===t.id));card.append(templateSVG(t));const name=document.createElement('strong');name.textContent=t.name;const size=document.createElement('small');size.textContent=`${fmt(t.widthFt)} × ${fmt(t.heightFt)} ft · X=${fmt(t.xFt)} ft`;card.append(name,size);card.onclick=()=>{templateId=t.id;for(const el of $('templateGrid').querySelectorAll('button'))el.setAttribute('aria-pressed',String(el.dataset.template===t.id));renderTemplateDetail();};$('templateGrid').append(card);}
 renderTemplateDetail();
}
T.validate();$('templateCount').textContent='· '+T.data.templates.length;$('templateIntro').textContent=`${T.data.templates.length} готовых контуров из ${T.data.models.length} моделей. Выбери форму, компонент и станцию текущей модели.`;
for(const m of T.data.models){const o=document.createElement('option');o.value=m.model;o.textContent=m.model+' · '+m.templates+' сеч.';$('templateModel').append(o);}
$('editorTab').onclick=()=>setAppTab('editor');$('templateTab').onclick=()=>setAppTab('library');
for(const id of ['editorTab','templateTab'])$(id).onkeydown=ev=>{if(['ArrowLeft','ArrowRight'].includes(ev.key)){ev.preventDefault();const tab=id==='editorTab'?'library':'editor';setAppTab(tab);$(tab==='library'?'templateTab':'editorTab').focus();}};
for(const id of ['templateSearch','templateModel','templateRole'])$(id).addEventListener(id==='templateSearch'?'input':'change',renderTemplateLibrary);
$('templateTarget').onchange=()=>renderTemplateTargets(true);for(const id of ['templateStation','templateSizeMode'])$(id).onchange=updateTemplateApplyState;
$('templateSave').onclick=()=>download(JSON.stringify(T.data,null,2),'Aircraft_Section_Library_v1.json','application/json');
$('templateApply').onclick=()=>{try{const i=Number($('templateTarget').value),k=Number($('templateStation').value),t=T.data.templates.find(t=>t.id===templateId),q=C.clone(project);if(!t||!q.bodies[i]?.enabled||!q.bodies[i].sections[k])throw new Error('Выбери шаблон и станцию включённого компонента.');q.bodies[i].sections[k]=T.apply(q.bodies[i].sections[k],t,$('templateSizeMode').value);if(commit(q,()=>{active=i;cursor=q.bodies[i].sections[k].x;sectionZoom=1;})){setAppTab('editor');notice('Применено: '+t.name+'. Положение и повороты станции сохранены.');}}catch(e){notice(e.message,true);}};
$('enabled1').onchange=()=>{const q=C.clone(project);q.bodies[1].enabled=$('enabled1').checked;commit(q);};
$('cursorX').onchange=()=>setCursor(numeric($('cursorX').value));$('cursorRange').oninput=()=>setCursor(Number($('cursorRange').value));
$('addBothBtn').onclick=()=>{try{const q=C.clone(project);for(const b of q.bodies){const a=C.sectionAt(b,cursor);if(a&&a.index<0)C.addAt(b,cursor);}commit(q);}catch(e){notice(e.message,true);}};
$('modelName').onchange=()=>{const q=C.clone(project);q.name=$('modelName').value.trim();commit(q);};
$('undoBtn').onclick=()=>{if(!history.length)return;invalidateAudit();const prev=history.pop();project=prev.project;cursor=prev.cursor;active=prev.active;dirty=true;sync();};
$('newBtn').onclick=()=>$('newDialog').showModal();
$('closeNewDialog').onclick=()=>$('newDialog').close();
$('newFromVsp').onclick=()=>{$('newDialog').close();$('vspFileInput').click();};
$('demoBtn').onclick=()=>{if(dirty&&!confirm('Открыть учебный пример? Текущие изменения останутся в истории отмены.'))return;push();project=C.demo();cursor=12;sectionZoom=1;sync();$('newDialog').close();};
function download(content,name,mime){const url=URL.createObjectURL(new Blob([content],{type:mime})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}
$('saveBtn').onclick=()=>{const e=C.validate(project);if(e.length){notice(e[0],true);return;}download(JSON.stringify(project,null,2),project.name+'_sections.json','application/json');dirty=false;sync(false);notice('Проект с обоими телами сохранён в JSON.');};
$('openBtn').onclick=()=>$('fileInput').click();
$('fileInput').onchange=async ev=>{const f=ev.target.files[0];if(!f)return;const request=++loadRequest;try{if(f.size>80e6)throw new Error('Предел размера JSON — 80 МБ.');const text=await f.text();if(request!==loadRequest)return;let raw;try{raw=JSON.parse(text.replace(/^\uFEFF/,''));}catch{throw new Error('Файл не содержит корректный JSON. Выбери рабочий проект, сохранённый кнопкой «Сохранить JSON».');}const q=C.importProject(raw,false);VSPImport.verifySource(q);if(dirty&&!confirm('Открыть JSON? Изменения текущего проекта останутся в истории отмены.'))return;installProject(q);if(raw.schema===1)notice('Проект 0.1 открыт без изменения сечений. Мотогондола выключена; включи её, чтобы добавить второе тело.');}catch(e){if(request===loadRequest)openError('Не удалось открыть '+f.name+': '+e.message);}finally{ev.target.value='';}};
$('exportBtn').onclick=()=>{try{if(project.source){const xml=VSPImport.write(project);download(xml,project.name+'_edited.vsp3','application/xml');notice('Скачан VSP3 с правками выбранных тел. Остальные компоненты сохранены в исходной модели.');return;}const script=C.exportScript(project),name=project.name+'_sections.vspscript';download(script,name,'text/plain;charset=utf-8');$('scriptName').textContent=name;$('exportDialog').showModal();}catch(e){notice(e.message,true);}};$('closeDialog').onclick=()=>$('exportDialog').close();
function selectedImportStatus(){
 if(!vspDraft)return;const selection=[$('vspBody0').value,$('vspBody1').value],units=$('vspUnits').value;
 try{const info=VSPImport.scan(vspDraft.xml,units),messages=selection[0]===selection[1]?['Выбери два разных компонента FUSELAGE.']:info.candidates.filter(c=>selection.includes(c.id)&&c.error).map(c=>c.error);$('vspError').textContent=messages.join('\n');$('vspPreparePanel').open=messages.some(t=>/PCHIP|Подготов|преобразование|типа /i.test(t));$('vspAccept').disabled=selection[0]===selection[1];}catch(e){$('vspError').textContent=e.message;}
}
function vspSelectors(){const info=vspDraft.info;for(let i=0;i<2;i++){const select=$('vspBody'+i);select.replaceChildren();info.candidates.forEach(c=>{const o=document.createElement('option');o.value=c.id;o.textContent=`${c.name} · L=${c.length===null?'?':fmt(c.length)} · ${c.sections} сеч.${c.error?' · нужна подготовка/проверка':''}`;select.append(o);});select.value=info.selection[i]||'';}$('vspUnits').value=vspDraft.units||'ft';$('vspSummary').textContent=`${vspDraft.name}: ${info.total} компонентов, ${info.candidates.length} тел FUSELAGE. Выбери исходные единицы. Остальные компоненты сохраняются.`;selectedImportStatus();}
function acceptVsp(){const selection=[$('vspBody0').value,$('vspBody1').value],units=$('vspUnits').value;try{const q=VSPImport.open(vspDraft.xml,vspDraft.name,selection,units);if(dirty&&!confirm('Открыть VSP3? Изменения текущего проекта останутся в истории отмены.'))return;installProject(q);$('vspDialog').close();notice(`Загружены ${q.bodies[0].name} и ${q.bodies[1].name}. Исходные единицы: ${units}; редактор показывает футы. Сглаживание и настройки двигателя сохранены.`);}catch(e){$('vspError').textContent=e.message;}}
$('openVspBtn').onclick=()=>$('vspFileInput').click();$('vspFileInput').onchange=async ev=>{const f=ev.target.files[0];if(!f)return;const request=++loadRequest;try{if(f.size>40e6)throw new Error('Предел размера VSP3 — 40 МБ.');const xml=await f.text();if(request!==loadRequest)return;const info=VSPImport.scan(xml);if(info.candidates.length<2)throw new Error('Нужно минимум два компонента FUSELAGE: фюзеляж и мотогондола.');vspDraft={xml,name:f.name,info,units:'ft'};vspSelectors();if(!$('vspDialog').open)$('vspDialog').showModal();}catch(e){if(request===loadRequest)openError('Не удалось открыть '+f.name+': '+e.message);}finally{ev.target.value='';}};
$('selectSourceBtn').onclick=()=>{const s=project.source;if(!s)return;try{vspDraft={xml:s.xml,name:s.name,info:VSPImport.scan(s.xml,s.units),units:s.units};vspDraft.info.selection=s.selection;vspSelectors();$('vspDialog').showModal();}catch(e){openError(e.message);}};
for(const id of ['vspBody0','vspBody1','vspUnits'])$(id).onchange=selectedImportStatus;
$('closeLoadError').onclick=()=>$('loadErrorDialog').close();
$('vspAccept').onclick=acceptVsp;$('vspCancel').onclick=()=>$('vspDialog').close();
$('vspPrepare').onclick=()=>{try{const chosen=[$('vspBody0').value,$('vspBody1').value];if(chosen[0]===chosen[1])throw new Error('Выбери два разных тела.');download(VSPImport.preparation(vspDraft.xml,chosen),'SectionStudio_prepare.zip','application/zip');$('vspError').textContent='Распакуй ZIP. Перетащи Prepare_sections.vspscript на Run_OpenVSP.bat, укажи OpenVSP и открой полученный Source_prepared.vsp3 здесь. Проверь форму после преобразования.';}catch(e){$('vspError').textContent=e.message;}};
function stopWorker(w){if(w){w.terminate();URL.revokeObjectURL(w._auditURL);}}
function invalidateAudit(){auditEpoch++;auditReport=null;auditUpdating=true;clearTimeout(auditFullTimer);clearTimeout(auditLocalTimer);auditFullTimer=auditLocalTimer=0;stopWorker(auditFullWorker);stopWorker(auditLocalWorker);auditFullWorker=auditLocalWorker=null;auditLocalBusy=auditLocalPending=false;renderAudit();}
function auditSettings(){const minGap=numeric($('auditGap').value),angleLimit=numeric($('auditAngle').value);if(!Number.isFinite(minGap)||minGap<0||minGap>10)throw new Error('Порог зазора должен быть от 0 до 10 ft.');if(!Number.isFinite(angleLimit)||angleLimit<5||angleLimit>89)throw new Error('Порог резкого перехода должен быть от 5 до 89°.');return {minGap,angleLimit};}
function auditMeta(){
 const source=project.source,ids=project.bodies.map(b=>(b.sourceId||'')+'#'+Number(b.enabled)).join(',');if(auditCachedMeta.xml===source?.xml&&auditCachedMeta.ids===ids)return auditCachedMeta.meta;
 const meta={caps:[{},{}],otherComponents:0,engineModes:project.bodies.map(b=>b.engineIO||0)};
 if(source){const doc=new DOMParser().parseFromString(source.xml,'application/xml'),child=(n,name)=>Array.from(n?.children||[]).find(c=>c.tagName===name),vehicle=child(doc.documentElement,'Vehicle'),geoms=Array.from(vehicle?.children||[]).filter(c=>c.tagName==='Geom');meta.otherComponents=Math.max(0,geoms.length-project.bodies.filter(b=>b.enabled).length);project.bodies.forEach((b,i)=>{const geom=geoms.find(g=>child(child(g,'ParmContainer'),'ID')?.textContent.trim()===b.sourceId),end=child(child(geom,'ParmContainer'),'EndCap');for(const [key,tag] of [['front','CapUMinOption'],['rear','CapUMaxOption']]){const value=child(end,tag)?.getAttribute('Value');if(value!==null&&value!==undefined)meta.caps[i][key]=Number(value);}});}
 auditCachedMeta={xml:source?.xml,ids,meta};return meta;
}
function auditSnapshot(local){return {project:{format:project.format,schema:project.schema,units:project.units,name:project.name,bodies:C.clone(project.bodies)},options:{...auditSettings(),...(local?{focusX:cursor}:{})},meta:auditMeta(),epoch:auditEpoch,revision:auditRevision};}
function auditWorker(){const source=$('coreScript').textContent+'\n'+$('auditScript').textContent+'\n'+`onmessage=function(e){const d=e.data,t=performance.now();try{const result=SectionAudit.checkGeometry(d.project,d.options,d.meta);postMessage({ok:true,result,epoch:d.epoch,revision:d.revision,elapsedMS:performance.now()-t});}catch(error){postMessage({ok:false,error:error.message,epoch:d.epoch,revision:d.revision});}};`;const url=URL.createObjectURL(new Blob([source],{type:'text/javascript'}));try{const worker=new Worker(url);worker._auditURL=url;return worker;}catch(e){URL.revokeObjectURL(url);throw e;}}
function acceptAudit(message){if(message.epoch!==auditEpoch)return;if(!message.ok){auditUpdating=false;notice('Проверка формы: '+message.error,true);renderAudit();return;}if(message.result.scope==='local'&&auditReport?.scope==='full'&&auditReport.revision===auditRevision)return;auditReport={...message.result,revision:message.revision,elapsedMS:message.elapsedMS};if(message.result.scope==='full'&&message.revision===auditRevision)auditUpdating=false;renderAudit();schedule();}
function requestLocalAudit(){
 if(!$('auditAuto').checked)return;if(auditLocalTimer)return;
 auditLocalTimer=setTimeout(()=>{auditLocalTimer=0;if(auditLocalBusy){auditLocalPending=true;return;}let data;try{data=auditSnapshot(true);}catch(e){notice(e.message,true);return;}
  if(auditFallback){const t=performance.now();try{acceptAudit({ok:true,result:SectionAudit.checkGeometry(data.project,data.options,data.meta),epoch:data.epoch,revision:data.revision,elapsedMS:performance.now()-t});}catch(e){notice(e.message,true);}return;}
  try{if(!auditLocalWorker){auditLocalWorker=auditWorker();const worker=auditLocalWorker;auditLocalWorker.onmessage=e=>{if(auditLocalWorker!==worker)return;auditLocalBusy=false;acceptAudit(e.data);if(auditLocalPending){auditLocalPending=false;requestLocalAudit();}};auditLocalWorker.onerror=()=>{if(auditLocalWorker!==worker)return;stopWorker(auditLocalWorker);auditLocalWorker=null;auditLocalBusy=false;auditFallback=true;requestLocalAudit();};}auditLocalBusy=true;auditLocalWorker.postMessage(data);}catch(e){auditLocalBusy=false;auditFallback=true;requestLocalAudit();}
 },70);
}
function runFullAudit(){
 clearTimeout(auditFullTimer);auditFullTimer=0;let data;try{data=auditSnapshot(false);}catch(e){notice(e.message,true);return;}stopWorker(auditFullWorker);auditFullWorker=null;auditUpdating=true;renderAudit();
 const fallback=()=>{const t=performance.now();setTimeout(()=>{try{acceptAudit({ok:true,result:SectionAudit.checkGeometry(data.project,data.options,data.meta),epoch:data.epoch,revision:data.revision,elapsedMS:performance.now()-t});}catch(e){notice(e.message,true);}},0);};
 try{auditFullWorker=auditWorker();const worker=auditFullWorker;auditFullWorker.onmessage=e=>{if(auditFullWorker!==worker)return;stopWorker(auditFullWorker);auditFullWorker=null;if(e.data.revision===auditRevision)acceptAudit(e.data);};auditFullWorker.onerror=()=>{if(auditFullWorker!==worker)return;stopWorker(auditFullWorker);auditFullWorker=null;fallback();};auditFullWorker.postMessage(data);}catch(e){fallback();}
}
function changedAuditGeometry(){
 const key=JSON.stringify(project.bodies),xml=project.source?.xml;const changed=key!==auditLastKey||xml!==auditLastXML;
 if(changed){auditLastKey=key;auditLastXML=xml;auditRevision++;auditUpdating=true;stopWorker(auditFullWorker);auditFullWorker=null;clearTimeout(auditFullTimer);auditFullTimer=0;}
 if($('auditAuto').checked&&(changed||!auditReport)){requestLocalAudit();if(!auditFullTimer)auditFullTimer=setTimeout(runFullAudit,600);}else if(changed){auditReport=null;auditUpdating=false;}renderAudit();
}
function riskAt(x,body){let result=null;for(const issue of auditReport?.issues||[]){const pad=Math.max(.0025,(project.bodies[body].sections.at(-1).x-project.bodies[body].sections[0].x)/128);if(issue.body.includes(body)&&x>=issue.x0-pad&&x<=issue.x1+pad&&(issue.severity==='error'||!result))result=issue.severity;}return result;}
function paintStationRisks(){for(const bt of $('stationList').children){const x=Number(bt.dataset.x),a=riskAt(x,0),b=riskAt(x,1),severity=a==='error'||b==='error'?'error':a||b;bt.classList.toggle('risky-error',severity==='error');bt.classList.toggle('risky-warning',severity==='warning');bt.title=severity?'В этой зоне найден геометрический риск — смотри предупреждения':'';}}
function renderAudit(){
 const report=auditReport,el=$('auditSummary');el.className='audit-summary';$('auditSave').disabled=!report||report.revision!==auditRevision;
 if(!report){el.textContent=$('auditAuto').checked?'Проверяется текущая зона…':'Автопроверка выключена';$('auditScope').textContent='Предварительная форма выбранных тел';$('auditList').replaceChildren();const empty=document.createElement('div');empty.className='audit-empty';empty.textContent=$('auditAuto').checked?'Изменяй сечения: предупреждения обновляются автоматически.':'Нажми «Проверить всю длину» или включи проверку в реальном времени.';$('auditList').append(empty);paintStationRisks();return;}
 const s=report.summary,scope=report.scope==='local'?'текущая зона':'вся длина',updating=auditUpdating?' · обновляется':'';el.textContent=`Рисков: ${s.errors} · предупреждений: ${s.warnings} · ${scope}${updating}`;if(s.errors)el.classList.add('error');else if(s.warnings)el.classList.add('warning');
 $('auditScope').textContent=`${report.scope==='local'?'Текущая зона и переходы станций':'Вся длина'} · ${s.planes} плоскостей · ${s.contourSegments} отрезков на контур${s.otherComponents?` · ещё ${s.otherComponents} компонентов не проверены`:''}${s.engineModesIgnored?' · двигательные обрезки не проверены':''}${s.instances>s.bodies?' · зеркальные копии учтены':''}${s.tiltedBodiesSkipped?' · наклонные тела: пространственный зазор не проверен':''}`;
 const list=$('auditList');list.replaceChildren();if(!report.issues.length){const empty=document.createElement('div');empty.className='audit-empty';empty.textContent=report.summary.tiltedBodiesSkipped?'Проверка неполная: пространственный зазор и контуры наклонных тел не проверены. В проверенной части выборки риски не обнаружены.':report.scope==='local'?'В текущей зоне по этой выборке риски не обнаружены. После паузы проверяется вся длина.':'На предварительной выборке риски не обнаружены. Это не проверка расчётной сетки и не гарантия запуска решателя.';list.append(empty);}
 for(const issue of report.issues){const bt=document.createElement('button');bt.className='audit-issue '+issue.severity;bt.dataset.code=issue.code;const title=document.createElement('strong');title.textContent=issue.title;const place=document.createElement('span');place.className='issue-place';place.textContent=issue.x0===issue.x1?`X = ${fmt(issue.x)} ft`:`X = ${fmt(issue.x0)}…${fmt(issue.x1)} ft`;if(issue.distance!==undefined)place.textContent+=` · зазор ≈ ${fmt(issue.distance)} ft`;const detail=document.createElement('span');detail.textContent=issue.detail;const action=document.createElement('span');action.textContent=issue.action;bt.append(title,place,detail,action);bt.onclick=()=>{active=issue.body[0];setCursor(issue.x);};list.append(bt);}paintStationRisks();
}
$('auditAuto').onchange=()=>{invalidateAudit();if($('auditAuto').checked)changedAuditGeometry();else{auditUpdating=false;renderAudit();schedule();}};
$('auditRun').onclick=runFullAudit;
for(const id of ['auditGap','auditAngle'])$(id).oninput=()=>{try{auditSettings();$(id).classList.remove('invalid');invalidateAudit();changedAuditGeometry();}catch(e){$(id).classList.add('invalid');notice(e.message,true);}};
$('auditSave').onclick=()=>{if(!auditReport||auditReport.revision!==auditRevision)return;download(JSON.stringify({program:'Section Studio 0.7',project:project.name,units:'ft',aerodynamicSolverRun:false,report:auditReport},null,2),project.name+'_geometry_risks.json','application/json');};
function ctx(id){const el=$(id),w=el.clientWidth||400,h=el.clientHeight||300,dpr=devicePixelRatio||1;if(el.width!==Math.round(w*dpr)||el.height!==Math.round(h*dpr)){el.width=Math.round(w*dpr);el.height=Math.round(h*dpr);}const c=el.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,w,h);return [c,w,h];}
function line(c,p,col='#879da8',width=1,closed=false){if(!p.length)return;c.beginPath();p.forEach((q,i)=>i?c.lineTo(q[0],q[1]):c.moveTo(q[0],q[1]));if(closed)c.closePath();c.strokeStyle=col;c.lineWidth=width;c.stroke();}
function fill(c,p,col,stroke=false){if(!p.length)return;c.beginPath();p.forEach((q,i)=>i?c.lineTo(q[0],q[1]):c.moveTo(q[0],q[1]));c.closePath();c.fillStyle=col;c.fill();if(stroke){c.strokeStyle=col;c.lineWidth=.35;c.stroke();}}
function text(c,t,x,y,col='#718391',align='left'){c.fillStyle=col;c.font='11px system-ui';c.textAlign=align;c.fillText(t,x,y);}
function schedule(){if(!frame)frame=requestAnimationFrame(()=>{frame=0;if(appTab!=='editor')return;draw3d();drawSection();drawPlane('sideView',2);drawPlane('topView',1);});}
function draw3d(){
 const [c,w,h]=ctx('view3d');c.fillStyle='#f8fbfc';c.fillRect(0,0,w,h);const scenes=displayMeshes(),flat=scenes.flatMap(s=>s.mesh.flat());if(!flat.length)return;
 const r=ranges(),midX=(r.min+r.max)/2,zs=flat.map(p=>p[2]),midZ=(Math.min(...zs)+Math.max(...zs))/2;
 const raw=p=>{const x=p[0]-midX,y=p[1],z=p[2]-midZ,xx=x*Math.cos(camera.a)+y*Math.sin(camera.a),d=-x*Math.sin(camera.a)+y*Math.cos(camera.a);return [xx,z*Math.cos(camera.e)-d*Math.sin(camera.e),d*Math.cos(camera.e)+z*Math.sin(camera.e)];};
 const pts=flat.map(raw),xx=pts.map(p=>p[0]),zz=pts.map(p=>p[1]),scale=Math.min((w-55)/Math.max(.1,Math.max(...xx)-Math.min(...xx)),(h-58)/Math.max(.1,Math.max(...zz)-Math.min(...zz)))*camera.zoom;
 const proj=p=>{const q=raw(p);return [w/2+q[0]*scale,h/2-q[1]*scale,q[2]];},faces=[];
 scenes.forEach(({mesh,body:b})=>{for(let i=0;i<mesh.length-1;i++)for(let j=0;j<mesh[i].length-1;j++){
  const a=[mesh[i][j],mesh[i+1][j],mesh[i+1][j+1],mesh[i][j+1]],p=a.map(proj),u=a[1].map((v,k)=>v-a[0][k]),v=a[3].map((v,k)=>v-a[0][k]),n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]],sh=.54+.42*Math.abs((-.35*n[0]-.5*n[1]+.79*n[2])/(Math.hypot(...n)||1)),risk=riskAt((a[0][0]+a[1][0])/2,b),rgb=(risk==='error'?[219,92,84]:risk==='warning'?[226,177,64]:b?[219,159,91]:[107,160,178]).map(v=>Math.round(v*sh));faces.push({p,depth:p.reduce((v,q)=>v+q[2],0)/4,color:`rgb(${rgb.join(',')})`});}
  if(mesh.length)for(const ix of [0,mesh.length-1]){const p=mesh[ix].slice(0,-1).map(proj);faces.push({p,depth:p.reduce((a,q)=>a+q[2],0)/p.length,color:b?'#b2824e':'#648b9c'});}
 });
 faces.sort((a,b)=>a.depth-b.depth);faces.forEach(f=>fill(c,f.p,f.color,true));
 for(const issue of (auditReport?.issues||[]).slice(0,80)){const color=issue.severity==='error'?'#c84542':'#c4892a';for(const body of issue.body){for(const r of C.ringsAt(project.bodies[body],issue.x,64)){c.setLineDash([4,3]);line(c,r.map(proj),color,2);c.setLineDash([]);}}}
 project.bodies.forEach((b,i)=>{const a=exact(i);if(a)for(const r of C.ringsAt(b,cursor,96)){c.setLineDash(a.index<0?[4,3]:[]);line(c,r.map(proj),COL[i],2);c.setLineDash([]);}});
 text(c,`Общая плоскость X = ${fmt(cursor)} ft`,13,22);
 const org=[w-45,h-30];for(const [name,p,col] of [['X',[1,0,0],'#ca7658'],['Y',[0,1,0],'#469875'],['Z',[0,0,1],'#548bd0']]){const q=raw([midX+p[0],p[1],midZ+p[2]]);line(c,[org,[org[0]+23*q[0],org[1]-23*q[1]]],col,1.5);text(c,name,org[0]+31*q[0],org[1]-31*q[1],col,'center');}
}
function drawSection(){
 const [c,w,h]=ctx('sectionView'),aa=project.bodies.map((b,i)=>exact(i)),valid=aa.filter(Boolean);handles=[];
 if(!valid.length){text(c,'В этой плоскости нет компонентов',w/2,h/2,'#758690','center');sectionMap=null;gap={state:'unavailable'};showGap();return;}
 const allRings=project.bodies.flatMap(b=>C.ringsAt(b,cursor,96)).flat(),minY=Math.min(...allRings.map(p=>p[1]))-.05,maxY=Math.max(...allRings.map(p=>p[1]))+.05,half=Math.max(.2,(maxY-minY)/2),minZ=Math.min(...allRings.map(p=>p[2])),maxZ=Math.max(...allRings.map(p=>p[2])),span=Math.max(.4,maxZ-minZ),scale=Math.min((w-80)/(2*half),(h-70)/span)*sectionZoom;
 const m=dragPoint?.map||{ox:w/2-(minY+maxY)/2*scale,oy:h/2+(minZ+maxZ)/2*scale,scale};sectionMap=m;
 const coord=(y,z)=>[m.ox+y*m.scale,m.oy-z*m.scale],world=p=>[(p[0]-m.ox)/m.scale,(m.oy-p[1])/m.scale],lo=world([30,h-26]),hi=world([w-15,22]),rawStep=Math.max(half*2,span)/6,step=Math.pow(10,Math.floor(Math.log10(rawStep)))*([1,2,5,10].find(k=>k*Math.pow(10,Math.floor(Math.log10(rawStep)))>=rawStep)||10);
 for(let y=Math.ceil(lo[0]/step)*step;y<=hi[0];y+=step){const xx=coord(y,0)[0];line(c,[[xx,20],[xx,h-24]],Math.abs(y)<1e-8?'#a9bec8':'#edf1f4');text(c,fmt(y),xx,h-8,'#8a9da7','center');}
 for(let z=Math.ceil(lo[1]/step)*step;z<=hi[1];z+=step){const yy=coord(0,z)[1];line(c,[[30,yy],[w-15,yy]],Math.abs(z)<1e-8?'#a9bec8':'#edf1f4');text(c,fmt(z),27,yy+4,'#8a9da7','right');}
 text(c,'Z, ft',10,13);text(c,'Y, ft',w-8,h-27,'#718391','right');
 aa.forEach((a,i)=>{if(!a)return;const s=a.section,risk=riskAt(cursor,i);C.ringsAt(project.bodies[i],cursor,160).forEach((ring,k)=>{const r=ring.map(p=>coord(p[1],p[2]));fill(c,r,LIGHT[i]);c.setLineDash(a.index<0||k?[5,4]:[]);line(c,r,risk==='error'?'#c84542':risk==='warning'?'#c4892a':COL[i],risk?3:2,true);c.setLineDash([]);});
  if(!s.width){const p=coord(s.y||0,s.z);c.beginPath();c.arc(...p,4,0,7);c.fillStyle=COL[i];c.fill();return;}
  if(a.index>=0&&C.localPoint(s,s.y||0,s.z)){const pp=s.points.map(p=>{const q=C.point3(s,p);return coord(q[1],q[2]);});line(c,pp,i?'#dec2a8':'#b5cfd8',.8);for(const k of C.editable(s)){const p=pp[k];handles.push({body:i,index:k,p});c.beginPath();c.arc(p[0],p[1],k%3===0?4.7:3.6,0,7);c.fillStyle=k%3===0?COL[i]:'#fff';c.fill();c.strokeStyle=COL[i];c.lineWidth=1.2;c.stroke();}}
 });
 const nearby=(auditReport?.issues||[]).filter(issue=>issue.body.some(i=>riskAt(cursor,i))&&Math.abs(issue.x-cursor)<=Math.max(.01,(ranges().max-ranges().min)/128));for(const issue of nearby){if(!issue.point)continue;const p=coord(issue.point[1],issue.point[2]);c.beginPath();c.arc(p[0],p[1],8,0,Math.PI*2);c.strokeStyle=issue.severity==='error'?'#c84542':'#c4892a';c.lineWidth=2;c.stroke();}
 const ga=aa[0],gb=aa[1],gapSegments=dragPoint?64:160;gap={state:'unavailable'};if(project.bodies.some(b=>b.enabled&&b.sections.some(s=>Math.abs(s.pitch||0)>1e-8||Math.abs(s.yaw||0)>1e-8)))gap={state:'tilted'};else if(ga&&gb){let best=null;for(const a of C.ringsAt(project.bodies[0],cursor,gapSegments))for(const b of C.ringsAt(project.bodies[1],cursor,gapSegments)){const g=SectionAudit.polygonGap(a.slice(0,-1).map(p=>p.slice(1)),b.slice(0,-1).map(p=>p.slice(1)),1e-8);if(!best||g.state==='overlap'&&best.state!=='overlap'||g.distance<best.distance)best=g;}gap={...best,exact:ga.index>=0&&gb.index>=0,samples:gapSegments};};if(gap.a&&gap.b){const p=coord(...gap.a),q=coord(...gap.b);line(c,[p,q],gap.state==='overlap'?'#ce3b39':'#64794c',2);for(const pt of [p,q]){c.beginPath();c.arc(pt[0],pt[1],3,0,7);c.fillStyle=gap.state==='overlap'?'#ce3b39':'#64794c';c.fill();}}
 showGap();
}
function showGap(){const el=$('gapReadout');el.replaceChildren();el.className='gap';let title;
 if(gap.state==='unavailable'){title='Для зазора оба тела должны присутствовать в этой плоскости.';el.classList.add('neutral');}
 else if(gap.state==='tilted'){title='Наклонные станции: показана проекция на Y / Z. Поперечный зазор здесь не вычисляется.';el.classList.add('neutral');}
 else if(gap.state==='overlap'){title='Перекрытие сечений — области тел пересекаются.';el.classList.add('warn');}
 else if(gap.state==='touching'){title='Контакт контуров: положительный зазор не обнаружен.';el.classList.add('warn');}
 else title=`Зазор в сечении ≈ ${gap.distance.toFixed(5)} ft · ${(gap.distance*304.8).toFixed(2)} мм`;
 el.append(document.createTextNode(title));if(gap.state!=='unavailable'&&gap.state!=='tilted'){const note=document.createElement('small');note.textContent=(gap.exact?'По заданным сечениям':'По промежуточной форме')+` · выборка ${gap.samples||160} отрезков на контур · только выбранная плоскость`;el.append(note);}
}
function drawPlane(id,axis){const [c,w,h]=ctx(id),scenes=displayMeshes(),flat=scenes.flatMap(s=>s.mesh.flat());if(!flat.length)return;const r=ranges(),mins=[1,2].map(k=>Math.min(...flat.map(p=>p[k]))),maxs=[1,2].map(k=>Math.max(...flat.map(p=>p[k]))),scale=Math.min((w-58)/(r.max-r.min),(h-35)/Math.max(.5,maxs[0]-mins[0],maxs[1]-mins[1])),ox=30+(w-58-(r.max-r.min)*scale)/2,oy=(h-18)/2+(maxs[axis-1]+mins[axis-1])/2*scale,xy=(x,z)=>[ox+(x-r.min)*scale,oy-z*scale];planeMaps[id]={ox,scale,min:r.min};
 scenes.forEach(({mesh:rows,body:b,mirror})=>{if(!rows.length)return;const upper=rows.map(row=>xy(row[0][0],Math.max(...row.map(p=>p[axis])))),lower=rows.map(row=>xy(row[0][0],Math.min(...row.map(p=>p[axis]))));const pp=upper.concat(lower.reverse());fill(c,pp,LIGHT[b]);line(c,pp,COL[b],1.2,true);project.bodies[b].sections.forEach(s=>{const rr=C.ring(s,64).map(p=>mirror?[p[0],-p[1],p[2]]:p),low=Math.min(...rr.map(p=>p[axis])),high=Math.max(...rr.map(p=>p[axis]));line(c,[xy(s.x,low),xy(s.x,high)],b?'#e2c6a9':'#a8c8d3',.7);});});
 for(const issue of (auditReport?.issues||[]).slice(0,80)){const x0=xy(issue.x0,0)[0],x1=xy(issue.x1,0)[0];c.fillStyle=issue.severity==='error'?'rgba(200,69,66,.13)':'rgba(196,137,42,.13)';c.fillRect(x0-2,12,Math.max(4,x1-x0+4),h-33);}
 const px=xy(cursor,0)[0];line(c,[[px,8],[px,h-20]],'#854f97',1.5);text(c,fmt(cursor),px,11,'#854f97','center');const L=r.max-r.min,step=L>20?5:L>10?2:1;for(let x=Math.ceil(r.min/step)*step;x<=r.max;x+=step)text(c,fmt(x),xy(x,0)[0],h-4,'#81959e','center');text(c,'X, ft',w-5,h-4,'#81959e','right');
}
const local=(ev,el)=>{const r=el.getBoundingClientRect();return [ev.clientX-r.left,ev.clientY-r.top];};
$('sectionView').addEventListener('pointerdown',ev=>{if(!sectionMap)return;const p=local(ev,ev.currentTarget);let best=12,hit=null;for(const h of handles){const d=Math.hypot(p[0]-h.p[0],p[1]-h.p[1]);if(d<best-1e-6||(hit&&Math.abs(d-best)<1e-6&&h.body===active)){best=d;hit=h;}}if(hit){push();active=hit.body;dragPoint={body:hit.body,index:hit.index,section:exact(hit.body).index,map:{...sectionMap}};ev.currentTarget.setPointerCapture(ev.pointerId);}});
$('sectionView').addEventListener('pointermove',ev=>{if(!dragPoint)return;const d=dragPoint,m=d.map,s=project.bodies[d.body].sections[d.section],p=local(ev,ev.currentTarget);const q=C.localPoint(s,(p[0]-m.ox)/m.scale,(m.oy-p[1])/m.scale);if(!q)return;C.movePoint(s,d.index,q[0],q[1]);meshes=project.bodies.map(b=>C.loft(b,32,32));$('shape'+d.body).value='custom';changedAuditGeometry();schedule();});
for(const name of ['pointerup','pointercancel'])$('sectionView').addEventListener(name,()=>{if(!dragPoint)return;dragPoint=null;sync();const e=C.validate(project);if(e.length)notice(e[0],true);});
$('zoomIn').onclick=()=>{sectionZoom=Math.min(6,sectionZoom*1.35);schedule();};$('zoomOut').onclick=()=>{sectionZoom=Math.max(.5,sectionZoom/1.35);schedule();};$('zoomFit').onclick=()=>{sectionZoom=1;schedule();};
$('view3d').addEventListener('pointerdown',ev=>{dragCamera={p:local(ev,ev.currentTarget),a:camera.a,e:camera.e};ev.currentTarget.setPointerCapture(ev.pointerId);});$('view3d').addEventListener('pointermove',ev=>{if(!dragCamera)return;const p=local(ev,ev.currentTarget);camera.a=dragCamera.a+(p[0]-dragCamera.p[0])*.009;camera.e=Math.max(-1.3,Math.min(1.3,dragCamera.e+(p[1]-dragCamera.p[1])*.008));schedule();});for(const n of ['pointerup','pointercancel'])$('view3d').addEventListener(n,()=>dragCamera=null);$('view3d').addEventListener('wheel',ev=>{ev.preventDefault();camera.zoom=Math.max(.4,Math.min(3,camera.zoom*Math.exp(-ev.deltaY*.001)));schedule();},{passive:false});$('resetView').onclick=()=>{camera={a:-.88,e:.48,zoom:1};schedule();};
for(const id of ['sideView','topView'])$(id).onclick=ev=>{const m=planeMaps[id];if(!m)return;const r=ranges(),x=Math.max(r.min,Math.min(r.max,(local(ev,ev.currentTarget)[0]-m.ox)/m.scale+m.min));const ss=project.bodies.filter(b=>b.enabled).flatMap(b=>b.sections.map(s=>s.x)),near=ss.reduce((a,b)=>Math.abs(b-x)<Math.abs(a-x)?b:a,ss[0]);setCursor(Math.abs(x-near)*m.scale<6?near:Number(x.toFixed(4)));};
window.addEventListener('resize',schedule);window.addEventListener('beforeunload',ev=>{if(dirty){ev.preventDefault();ev.returnValue='';}});
window.SectionStudio={getProject:()=>C.clone(project),setProject:p=>{const q=C.importProject(p);push();project=q;cursor=q.bodies[0].sections[Math.min(3,q.bodies[0].sections.length-1)].x;sectionZoom=1;sync();},setCursor,select:(i,b=0)=>setCursor(project.bodies[b].sections[i].x),getViewInfo:()=>C.clone({cursor,sectionMap,handles,gap}),getAudit:()=>auditReport?C.clone({...auditReport,currentRevision:auditRevision,updating:auditUpdating}):null,checkGeometry:runFullAudit,draw:()=>{draw3d();drawSection();drawPlane('sideView',2);drawPlane('topView',1);},exportScript:()=>C.exportScript(project),exportVSP:()=>VSPImport.write(project)};
sync();
})();
