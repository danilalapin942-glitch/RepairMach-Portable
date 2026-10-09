/* Local VSP3 import and surgical XML writeback. No network access. */
(function(root){
'use strict';
const C=root.SectionCore,child=(n,k)=>Array.from(n?.children||[]).find(e=>e.tagName===k),at=(n,p)=>p.split('/').reduce(child,n),txt=(n,p,d='')=>at(n,p)?.textContent.trim()??d;
function val(n,p,d=0){const a=at(n,p);if(!a)return d;const raw=a.getAttribute('Value');const v=raw===null||!raw.trim()?NaN:Number(raw);if(!Number.isFinite(v))throw new Error('Некорректное число VSP3: '+p);return v;}
const EPS=1e-8,TYPES={0:'Point',1:'Circle',2:'Ellipse',3:'SuperEllipse',4:'RoundedRectangle',5:'GeneralFuse',6:'FileFuse',11:'EditCurve'};
const factor=u=>({ft:1,m:1/.3048,mm:1/304.8,in:1/12}[u]);
const dimKey=(pc,k)=>Array.from(child(pc,'XSecCurve')?.children||[]).find(n=>n.tagName===k||n.tagName.endsWith('_'+k))?.tagName||k;
const dimension=(pc,k)=>val(pc,'XSecCurve/'+dimKey(pc,k));
function documentOf(xml){if(typeof xml!=='string'||xml.length>40e6)throw new Error('VSP3: предел размера 40 МБ.');if(/<!DOCTYPE|<!ENTITY/i.test(xml))throw new Error('XML с DTD или сущностями не поддерживается.');const d=new DOMParser().parseFromString(xml,'application/xml');if(d.querySelector('parsererror')||d.documentElement.tagName!=='Vsp_Geometry')throw new Error('Нужен файл .vsp3 из OpenVSP 3. Старый формат .vsp сначала сохрани как .vsp3.');return d;}
function geomRanges(xml){const result=[],stack=[];let start=-1;const re=/<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<\/?([A-Za-z_][\w:.-]*)\b[^>]*>/g;let m;while((m=re.exec(xml))){if(!m[1])continue;const close=m[0].startsWith('</'),self=m[0].endsWith('/>');if(close){if(m[1]==='Geom'&&stack.join('/')==='Vsp_Geometry/Vehicle/Geom')result.push({start,end:re.lastIndex});stack.pop();}else{if(m[1]==='Geom'&&stack.join('/')==='Vsp_Geometry/Vehicle')start=m.index;if(!self)stack.push(m[1]);}}return result;}
function parse(xml){const doc=documentOf(xml),vehicle=at(doc.documentElement,'Vehicle');if(!vehicle)throw new Error('В VSP3 нет Vehicle.');const geoms=Array.from(vehicle.children).filter(g=>g.tagName==='Geom'),ranges=geomRanges(xml);if(geoms.length!==ranges.length)throw new Error('Не удалось однозначно выделить компоненты VSP3.');const all=geoms.map((node,i)=>({node,id:txt(node,'ParmContainer/ID'),name:txt(node,'ParmContainer/Name'),type:txt(node,'GeomBase/TypeName'),parent:txt(node,'GeomBase/ParentID'),range:ranges[i]}));if(new Set(all.map(g=>g.id)).size!==all.length)throw new Error('В VSP3 повторяются идентификаторы компонентов.');return {doc,all,version:txt(doc.documentElement,'Version')};}
function checkBody(g,all){
 const n=g.node,p='ParmContainer/',reasons=[];
 for(const axis of ['X','Y','Z'])if(Math.abs(val(n,p+'XForm/'+axis+'_Rotation'))>EPS)reasons.push('поворот компонента '+axis);
 if(val(n,p+'Attach/Trans_Attach_Flag')!==0||val(n,p+'Attach/Rots_Attach_Flag')!==0)reasons.push('активная привязка к родительской геометрии');
 const planar=val(n,p+'Sym/Sym_Planar_Flag');
 if((planar!==0&&planar!==2)||val(n,p+'Sym/Sym_Axial_Flag')!==0)reasons.push('симметрия: поддерживается только одна зеркальная копия относительно XZ');
 if(planar===2&&g.parent!=='NONE'&&g.parent!=='')reasons.push('зеркальная копия относительно родительского компонента');
 if(Math.abs(val(n,p+'XForm/Scale',1)-val(n,p+'XForm/Last_Scale',1))>EPS)reasons.push('неприменённое масштабирование');
 if(![0,1,2,3].includes(val(n,p+'EngineModel/GeomIOType')))reasons.push('неизвестный режим двигателя');
 if(all.some(other=>{if(other.id===g.id)return false;let a=other;const seen=new Set();while(a&&a.parent!=='NONE'&&!seen.has(a.id)){seen.add(a.id);if(a.parent===g.id&&(val(other.node,p+'Attach/Trans_Attach_Flag')!==0||val(other.node,p+'Attach/Rots_Attach_Flag')!==0))return true;a=all.find(v=>v.id===a.parent);}return false;}))reasons.push('другие компоненты привязаны к этому телу');
 if(reasons.length)throw new Error(g.name+': пока не поддерживается '+reasons.join(', ')+'.');
}
function readBody(g,all,role,units){
 if(g.type.toLowerCase()!=='fuselage')throw new Error(g.name+': нужен компонент FUSELAGE.');
 checkBody(g,all);const mul=factor(units);if(!mul)throw new Error('Неизвестные единицы файла.');const n=g.node,L=val(n,'ParmContainer/Design/Length');if(!(L>0))throw new Error(g.name+': неверная длина.');
 const x0=val(n,'ParmContainer/XForm/X_Location'),y0=val(n,'ParmContainer/XForm/Y_Location'),z0=val(n,'ParmContainer/XForm/Z_Location'),surf=at(n,'FuselageGeom/XSecSurf');if(!surf)throw new Error(g.name+': нужен компонент FUSELAGE.');
 const nodes=Array.from(surf.children).filter(n=>n.tagName==='XSec'),sections=nodes.map((xs,index)=>{
  const q='ParmContainer/XSec/';if(Math.abs(val(xs,q+'Spin'))>EPS)throw new Error(g.name+': сечение '+(index+1)+' имеет Spin.');
  const cv=at(xs,'XSec/XSecCurve'),cp=at(cv,'ParmContainer'),type=Number(txt(cv,'XSecCurve/Type','-1')),group=child(cp,'XSecCurve');
  for(const [key,def] of [['Theta',0],['DeltaX',0],['DeltaY',0],['ShiftLE',0],['Scale',1]])if(Math.abs(val(cp,'XSecCurve/'+key,def)-def)>EPS)throw new Error(g.name+': преобразование '+key+' у сечения '+(index+1)+'.');
  for(const p of ['Chevron/Chevron_Type','Flap/TE_Flap_Flag','Trim/LE_Trim_Type','Trim/TE_Trim_Type','Close/LE_Close_Type','Close/TE_Close_Type'])if(val(cp,p)!==0)throw new Error(g.name+': модификатор '+p+' у сечения '+(index+1)+'.');
  let width=type===1?dimension(cp,'Diameter'):dimension(cp,'Width'),height=type===1?width:dimension(cp,'Height'),points=C.preset('ellipse'),knots,shape='ellipse',freeform=false,symmetric=true;
  if(type===0){width=height=0;shape='point';}
  else if(type===11){
   const num=Number(txt(cv,'EditCurveXSec/NumPts','0')),ct=val(cp,'XSecCurve/CurveType');if(val(cp,'XSecCurve/AbsoluteFlag')!==0)throw new Error(g.name+': EditCurve с абсолютными управляющими точками. Подготовь сечения через OpenVSP.');
   if(ct!==2&&ct!==0)throw new Error(g.name+': сечение '+(index+1)+' EditCurve PCHIP. Нажми «Скачать подготовку сечений» и выполни преобразование через OpenVSP.');
   let pp=Array.from({length:num},(_,i)=>[val(group,'X_'+i),val(group,'Y_'+i)]),uu=Array.from({length:num},(_,i)=>val(group,'U_'+i));
   if(!Number.isInteger(num)||pp.length<3||pp.length>(ct===0?129:385))throw new Error(g.name+': неподдерживаемое число точек EditCurve.');
   if(val(cp,'XSecCurve/CloseFlag',1)!==1)throw new Error(g.name+': сечение '+(index+1)+' имеет незамкнутый EditCurve. Замкни контур в OpenVSP.');
   if(uu.some((u,i)=>!Number.isFinite(u)||i&&u<=uu[i-1])||Math.abs(uu[0])>EPS||Math.abs(uu.at(-1)-1)>EPS)throw new Error(g.name+': сечение '+(index+1)+' имеет неверные узлы EditCurve.');
   for(let i=0;i<num;i++)if(Math.abs(val(group,'Z_'+i))>EPS||Math.abs(val(group,'R_'+i))>EPS)throw new Error(g.name+': пространственная кривая или скругления EditCurve. Подготовь сечения через OpenVSP.');
   if(ct===0){points=[];knots=[];for(let i=0;i<pp.length-1;i++){const a=pp[i],b=pp[i+1],u=uu[i],v=uu[i+1];for(let k=0;k<3;k++){points.push(a.map((x,j)=>x+(b[j]-x)*k/3));knots.push(u+(v-u)*k/3);}}points.push(pp.at(-1));knots.push(1);}
   else{points=pp;knots=uu;}shape='custom';freeform=true;
   const s={points,knots};symmetric=points.slice(0,-1).every((p,i)=>{const target=(1.5-knots[i])%1,j=knots.findIndex(u=>Math.abs(u-target)<1e-7);return j>=0&&Math.abs(p[0]+points[j][0])<1e-5&&Math.abs(p[1]-points[j][1])<1e-5;});
  }else if(type!==1&&type!==2)throw new Error(`${g.name}: сечение ${index+1} типа ${TYPES[type]||type}. Подготовь сечения через OpenVSP.`);
  return {x:(x0+val(xs,q+'XLocPercent')*L)*mul,y:(y0+val(xs,q+'YLocPercent')*L)*mul,z:(z0+val(xs,q+'ZLocPercent')*L)*mul,roll:val(xs,q+'XRotate'),pitch:val(xs,q+'YRotate'),yaw:val(xs,q+'ZRotate'),width:width*mul,height:height*mul,shape,points,...(knots?{knots,freeform,symmetric}:{}),sourceId:txt(xs,'ParmContainer/ID')};
 });
 const engineIO=val(n,'ParmContainer/EngineModel/GeomIOType'),endClosures=[];
 // A native coincident Point can close an end in its own plane. Retain it in XML,
 // but omit it from the single-valued axial preview instead of moving geometry.
 if(sections.length>3&&sections.at(-1).shape==='point'&&Math.abs(sections.at(-1).x-sections.at(-2).x)<EPS)endClosures.push(sections.pop());
 const body={id:role,name:g.name,enabled:true,strength:.7,preserveSkin:true,mirrorY:val(n,'ParmContainer/Sym/Sym_Planar_Flag')===2,engineIO,sourceId:g.id,sections,...(endClosures.length?{endClosures}:{})};const err=C.validateBody(body,false);if(err.length)throw new Error(g.name+': '+err[0]);return body;
}
function scan(xml,units='ft'){const p=parse(xml),candidates=p.all.filter(g=>g.type.toLowerCase()==='fuselage'&&at(g.node,'FuselageGeom'));let f=candidates.filter(g=>/^(fuselage|фюзеляж)(?:[ _\d-]|$)/i.test(g.name)),g=candidates.filter(g=>/^(gondola|nacelle|мотогондола|гондола)(?:[ _\d-]|$)/i.test(g.name));
 const length=g=>{try{return val(g.node,'ParmContainer/Design/Length');}catch{return null;}};
 let automatic=f.length===1&&g.length===1&&f[0].id!==g[0].id;const sorted=candidates.slice().sort((a,b)=>(length(b)||0)-(length(a)||0));
 const fId=f[0]?.id||sorted[0]?.id,gId=g.find(q=>q.id!==fId)?.id||sorted.find(q=>q.id!==fId)?.id;
 return {version:p.version,total:p.all.length,candidates:candidates.map(g=>{let error='';try{readBody(g,p.all,'fuselage',units);}catch(e){error=e.message;}return {id:g.id,name:g.name,length:length(g),sections:Array.from(at(g.node,'FuselageGeom/XSecSurf')?.children||[]).filter(n=>n.tagName==='XSec').length,error};}),selection:[fId,gId],automatic};
}
function open(xml,name,selection,units='ft'){
 if(!Array.isArray(selection)||selection.length!==2||selection.some(id=>typeof id!=='string'||!id)||selection[0]===selection[1])throw new Error('Выбери два разных компонента FUSELAGE.');const p=parse(xml);const bodies=selection.map((id,i)=>{const g=p.all.find(g=>g.id===id);if(!g)throw new Error('Компонент не найден в VSP3.');return readBody(g,p.all,i?'gondola':'fuselage',units);});
 const safe=String(name||'Model').replace(/\.[^.]+$/,'').replace(/[^A-Za-z0-9_]/g,'_').replace(/^([^A-Za-z])/,'Model_$1').slice(0,32)||'Model';const project={format:'section-studio',schema:2,units:'ft',name:safe,bodies,source:{kind:'vsp3',name:String(name||'Model.vsp3'),xml,units,selection,version:p.version}};return C.importProject(project,false);
}
function verifySource(project){
 const src=project.source;if(!src)return project;
 if(src.kind!=='vsp3'||typeof src.name!=='string'||typeof src.xml!=='string'||!factor(src.units))throw new Error('В JSON повреждена запись исходного VSP3. Открой исходный .vsp3 заново.');
 const initial=open(src.xml,src.name,src.selection,src.units);
 project.bodies.forEach((b,i)=>{const old=initial.bodies[i];if(b.sourceId!==old.sourceId)throw new Error('JSON: выбранное тело не соответствует исходному VSP3.');
  const valid=new Set(old.sections.map(s=>s.sourceId)),used=new Set();for(const s of b.sections){if(!s.sourceId)continue;if(!valid.has(s.sourceId)||used.has(s.sourceId))throw new Error('JSON: неверная или повторная ссылка на сечение VSP3.');used.add(s.sourceId);}
  if(!!b.mirrorY!==!!old.mirrorY)throw new Error('JSON: зеркальная копия отличается от исходной модели. Открой VSP3 заново.');
  if(b.engineIO!==undefined&&b.engineIO!==old.engineIO)throw new Error('JSON: режим двигателя отличается от исходного VSP3.');b.engineIO=old.engineIO;
  if(JSON.stringify(b.endClosures||[])!==JSON.stringify(old.endClosures||[]))throw new Error('JSON: изменены сохранённые торцевые станции. Открой VSP3 заново.');
 });return project;
}
function uid(len=11){const a=new Uint8Array(len);crypto.getRandomValues(a);return Array.from(a,x=>String.fromCharCode(65+x%26)).join('');}
function ensure(n,path){let cur=n;for(const k of path.split('/')){let e=child(cur,k);if(!e){e=n.ownerDocument.createElement(k);cur.append(e);}cur=e;}return cur;}
function put(n,path,value,id){const e=ensure(n,path);e.setAttribute('Value',Number(value).toPrecision(17));if(!e.hasAttribute('ID'))e.setAttribute('ID',id||uid());return e;}
function textPut(n,path,value){ensure(n,path).textContent=String(value);}
function replaceIDs(n){for(const e of [n,...n.querySelectorAll('*')]){if(e.hasAttribute('ID'))e.setAttribute('ID',uid());if(e.tagName==='ID')e.textContent=uid(10);}}
function setCurve(xs,s,old,same){
 let cv=at(xs,'XSec/XSecCurve'),oldPC=at(cv,'ParmContainer'),oldType=Number(txt(cv,'XSecCurve/Type','-1'));
 const unchangedShape=old&&s.shape===old.shape&&JSON.stringify(s.points)===JSON.stringify(old.points)&&JSON.stringify(s.knots||null)===JSON.stringify(old.knots||null);
 if(same&&unchangedShape){
  if(oldType===1&&Math.abs(s.width-s.height)<1e-10){put(oldPC,'XSecCurve/'+dimKey(oldPC,'Diameter'),s.width);return;}
  if(oldType!==1){if(s.width){put(oldPC,'XSecCurve/'+dimKey(oldPC,'Width'),s.width);put(oldPC,'XSecCurve/'+dimKey(oldPC,'Height'),s.height);textPut(cv,'XSecCurve/XSecCurveDriverGroup/ChoiceVec','0, 2, ');}return;}
 }
 // Shape changes use an explicit editable cubic curve; existing component IDs survive.
 const doc=xs.ownerDocument,newCv=doc.createElement('XSecCurve'),pc=oldPC?oldPC.cloneNode(true):doc.createElement('ParmContainer');
 const oldGroup=child(pc,'XSecCurve'),oldIDs={};for(const e of Array.from(oldGroup?.children||[]))oldIDs[e.tagName]=e.getAttribute('ID');oldGroup?.remove();
 textPut(pc,'ID',txt(oldPC,'ID')||uid(10));textPut(pc,'Name',s.width?'EditCurve':'Point');newCv.append(pc);
 textPut(newCv,'XSecCurve/Type',s.width?11:0);textPut(newCv,'XSecCurve/GroupName','XSecCurve');
 if(s.width){textPut(newCv,'EditCurveXSec/NumPts',s.points.length);textPut(newCv,'XSecCurve/XSecCurveDriverGroup/NumVar',4);textPut(newCv,'XSecCurve/XSecCurveDriverGroup/NumChoices',2);textPut(newCv,'XSecCurve/XSecCurveDriverGroup/ChoiceVec','0, 2, ');
  const vals={Width:s.width,Height:s.height,Depth:1,CurveType:2,ConvType:2,SymType:0,AbsoluteFlag:0,CloseFlag:1,Scale:1,DeltaX:0,DeltaY:0,Theta:0,ShiftLE:0};for(const [k,v] of Object.entries(vals))put(pc,'XSecCurve/'+k,v,oldIDs[k]);
  const u=C.knotsOf(s);s.points.forEach((p,i)=>{for(const [k,v] of Object.entries({X:p[0],Y:p[1],Z:0,U:u[i],R:0,G1:0,FixedU:0}))put(pc,'XSecCurve/'+k+'_'+i,v,oldIDs[k+'_'+i]);});
 }
 cv.replaceWith(newCv);
}
function skin(xs,angles,strength){const p='ParmContainer/XSec/';for(const k of ['AllSym','RLSym','TBSym'])put(xs,p+k,0);['Top','Right','Bottom','Left'].forEach((dir,j)=>{put(xs,p+'Continuity'+dir,1);for(const side of ['L','R'])for(const [key,v] of Object.entries({Angle:angles[j],Strength:strength,Slew:0,Curve:0})){put(xs,p+dir+side+key,v);put(xs,p+dir+side+key+'Set',key==='Curve'?0:1);}for(const key of ['Angle','Strength','Slew','Curve'])put(xs,p+dir+'LR'+key+'Eq',0);});}
function remapEngineStations(n,original,newSections){
 const mode=val(n,'ParmContainer/EngineModel/GeomIOType');if(!mode||original.length===newSections.length&&original.every((xs,i)=>txt(xs,'ParmContainer/ID')===txt(newSections[i],'ParmContainer/ID')))return;
 for(const side of ['Inlet','Outlet']){if(side==='Inlet'&&mode===3||side==='Outlet'&&mode===1)continue;
  for(const part of ['Face','Lip']){const path='ParmContainer/EngineModel/'+side+part+'Index',el=at(n,path);if(!el)continue;
   const k=val(n,path);if(!Number.isInteger(k)||k<0||k>=original.length)throw new Error('Неверный индекс станции двигателя: '+side+part+'.');
   const id=txt(original[k],'ParmContainer/ID'),j=newSections.findIndex(xs=>txt(xs,'ParmContainer/ID')===id);
   if(j<0)throw new Error('Сечение '+(k+1)+' используется станцией двигателя '+side+part+'. Верни сечение или измени станцию в OpenVSP.');
   if(j!==k)put(n,path,j);
  }
 }
}
function write(project){
 const errors=C.validate(project);if(errors.length)throw new Error(errors.join('\n'));verifySource(project);const src=project.source;if(!src||src.kind!=='vsp3')throw new Error('Сначала открой исходный VSP3.');const parsed=parse(src.xml),initial=open(src.xml,src.name,src.selection,src.units),mul=factor(src.units),replacements=[];
 project.bodies.forEach((b,i)=>{
  const old=initial.bodies[i];if(b.sourceId!==old.sourceId)throw new Error('Идентификаторы выбранных тел изменились. Открой исходный VSP3 заново.');if(!b.enabled||JSON.stringify(b)===JSON.stringify(old))return;
  const g=parsed.all.find(g=>g.id===b.sourceId),n=g.node.cloneNode(true),origin=b.sections[0].x/mul,L=(b.sections.at(-1).x-b.sections[0].x)/mul,y0=val(n,'ParmContainer/XForm/Y_Location'),z0=val(n,'ParmContainer/XForm/Z_Location');
  if(!(L>0))throw new Error('Длина тела должна быть положительной.');
  // Trans_Attach_Flag==NONE was checked on import, so relative/absolute agree.
  textPut(n,'ParmContainer/Name',b.name);put(n,'ParmContainer/Design/Length',L);put(n,'ParmContainer/XForm/X_Location',origin);put(n,'ParmContainer/XForm/X_Rel_Location',origin);
  const surf=at(n,'FuselageGeom/XSecSurf'),original=Array.from(surf.children).filter(e=>e.tagName==='XSec'),oldMap=new Map(original.map(e=>[txt(e,'ParmContainer/ID'),e]));
  const x=b.sections.map(s=>s.x),top=C.slopes(x,b.sections.map(s=>s.z+s.height/2)),bottom=C.slopes(x,b.sections.map(s=>s.z-s.height/2)),side=C.slopes(x,b.sections.map(s=>s.width/2));
  const used=new Set(),newSections=b.sections.map((s,k)=>{
   if(s.sourceId&&used.has(s.sourceId))throw new Error('Повторяется идентификатор сечения.');if(s.sourceId)used.add(s.sourceId);
   const oldNode=s.sourceId?oldMap.get(s.sourceId):null;if(s.sourceId&&!oldNode)throw new Error('Сечение не принадлежит выбранному телу.');const node=(oldNode||original[Math.min(k,original.length-1)]).cloneNode(true);if(!oldNode)replaceIDs(node);
   const converted={...s,x:s.x/mul,y:(s.y||0)/mul,z:s.z/mul,width:s.width/mul,height:s.height/mul},oldS=old.sections.find(q=>q.sourceId===s.sourceId),base=oldS?{...oldS,width:oldS.width/mul,height:oldS.height/mul}:null;
   put(node,'ParmContainer/XSec/XLocPercent',(converted.x-origin)/L);put(node,'ParmContainer/XSec/ZLocPercent',(converted.z-z0)/L);put(node,'ParmContainer/XSec/RefLength',L);
   const yp=(converted.y-y0)/L;if(at(node,'ParmContainer/XSec/YLocPercent')||Math.abs(yp)>EPS)put(node,'ParmContainer/XSec/YLocPercent',yp);
   for(const [k,param] of [['roll','XRotate'],['pitch','YRotate'],['yaw','ZRotate']])if(at(node,'ParmContainer/XSec/'+param)||s[k])put(node,'ParmContainer/XSec/'+param,s[k]||0);
   setCurve(node,converted,base,!!oldNode);
   if(b.preserveSkin===false||!oldNode){const aa=[Math.atan(top[k]),Math.atan(side[k]),-Math.atan(bottom[k]),Math.atan(side[k])].map(a=>a*180/Math.PI);skin(node,aa,b.strength);}
   return node;
  });
  for(const s of old.endClosures||[]){const node=oldMap.get(s.sourceId).cloneNode(true),last=b.sections.at(-1),base=old.sections.at(-1);put(node,'ParmContainer/XSec/XLocPercent',1);put(node,'ParmContainer/XSec/RefLength',L);put(node,'ParmContainer/XSec/ZLocPercent',((s.z+last.z-base.z)/mul-z0)/L);put(node,'ParmContainer/XSec/YLocPercent',(((s.y||0)+(last.y||0)-(base.y||0))/mul-y0)/L);newSections.push(node);}
  original.forEach(n=>n.remove());newSections.forEach(n=>surf.append(n));put(n,'ParmContainer/Index/ActiveXSec',Math.min(newSections.length-1,Math.max(0,val(n,'ParmContainer/Index/ActiveXSec'))));
  remapEngineStations(n,original,newSections);
  // End caps, sets, attachment settings, masses and all unrelated subtrees remain.
  replacements.push({...g.range,text:new XMLSerializer().serializeToString(n)});
 });
 let xml=src.xml;for(const r of replacements.sort((a,b)=>b.start-a.start))xml=xml.slice(0,r.start)+r.text+xml.slice(r.end);const updated=documentOf(xml);
 const definitions=d=>new Set([...Array.from(d.querySelectorAll('[ID]'),e=>e.getAttribute('ID')),...Array.from(d.querySelectorAll('ParmContainer > ID'),e=>e.textContent.trim())]);
 const before=definitions(parsed.doc),after=definitions(updated);
 for(const id of before)if(!after.has(id)&&/^[A-Za-z][A-Za-z0-9_]{5,40}$/.test(id)&&new RegExp('(?:^|[^A-Za-z0-9_])'+id+'(?=$|[^A-Za-z0-9_])').test(xml))throw new Error('Удаляемый параметр или сечение используется связью в исходном VSP3. Сохрани JSON и сначала измени эту связь в OpenVSP.');
 return xml;
}
function prepareScript(selection){if(selection.some(id=>!/^[A-Za-z0-9_]{1,40}$/.test(id)))throw new Error('Некорректный идентификатор компонента.');return '// Convert selected fuselage sections through the native OpenVSP API.\nvoid main(){\n ClearVSPModel();\n ReadVSPFile("Source.vsp3");\n array<string> ids={'+selection.map(s=>'"'+s+'"').join(',')+'};\n for(uint k=0;k<ids.length();k++){\n string g=ids[k]; string surf=GetXSecSurf(g,0);\n for(int i=0;i<GetNumXSec(surf);i++){string xs=GetXSec(surf,i); if(GetXSecShape(xs)==XS_POINT) continue;\n if(GetXSecShape(xs)!=XS_EDIT_CURVE){ConvertXSecToEdit(g,i); Update();} xs=GetXSec(surf,i); EditXSecConvertTo(xs,CEDIT); SetParmVal(GetXSecParm(xs,"AbsoluteFlag"),0); Update();}\n }\n if(GetNumTotalErrors()>0){while(GetNumTotalErrors()>0){ErrorObj e=PopLastError(); Print(e.GetErrorString());} Print("SECTION_STUDIO_ERROR");return;}\n WriteVSPFile("Source_prepared.vsp3",SET_ALL);\n if(GetNumTotalErrors()>0){Print("SECTION_STUDIO_ERROR");return;} Print("SECTION_STUDIO_SAVED: Source_prepared.vsp3");\n}\n';}
function zipStore(files){const enc=new TextEncoder(),chunks=[],central=[],table=Array.from({length:256},(_,i)=>{for(let j=0;j<8;j++)i=(i&1)?0xedb88320^(i>>>1):i>>>1;return i>>>0;}),crc=b=>{let c=0xffffffff;for(const n of b)c=table[(c^n)&255]^(c>>>8);return (c^0xffffffff)>>>0;};let offset=0;const u16=(v,o,x)=>v.setUint16(o,x,true),u32=(v,o,x)=>v.setUint32(o,x,true);for(const [name,content] of Object.entries(files)){const n=enc.encode(name),b=enc.encode(content),sum=crc(b),head=new Uint8Array(30+n.length),h=new DataView(head.buffer);u32(h,0,0x04034b50);u16(h,4,20);u16(h,6,0x800);u32(h,14,sum);u32(h,18,b.length);u32(h,22,b.length);u16(h,26,n.length);head.set(n,30);chunks.push(head,b);const c=new Uint8Array(46+n.length),v=new DataView(c.buffer);u32(v,0,0x02014b50);u16(v,4,20);u16(v,6,20);u16(v,8,0x800);u32(v,16,sum);u32(v,20,b.length);u32(v,24,b.length);u16(v,28,n.length);u32(v,42,offset);c.set(n,46);central.push(c);offset+=head.length+b.length;}
 const size=central.reduce((s,a)=>s+a.length,0),end=new Uint8Array(22),v=new DataView(end.buffer);u32(v,0,0x06054b50);u16(v,8,central.length);u16(v,10,central.length);u32(v,12,size);u32(v,16,offset);chunks.push(...central,end);const out=new Uint8Array(offset+size+22);let pos=0;chunks.forEach(c=>{out.set(c,pos);pos+=c.length;});return out;
}
function preparation(xml,selection){parse(xml);return zipStore({'Source.vsp3':xml,'Prepare_sections.vspscript':prepareScript(selection),'README.txt':'Extract this ZIP. Drag Prepare_sections.vspscript onto Run_OpenVSP.bat from Section Studio. Select your OpenVSP executable. Open Source_prepared.vsp3 in Section Studio. Conversion is performed by OpenVSP; compare the converted curves with the original before editing. Unsupported rotations, offsets, engine modes and attachments are not removed.\n'});}
root.VSPImport={scan,open,write,preparation,prepareScript,parse,readBody,verifySource};
})(typeof globalThis!=='undefined'?globalThis:this);
