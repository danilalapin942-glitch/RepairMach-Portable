/* Reusable local contours extracted from the user's previous aircraft models. */
(function(root){
 'use strict';
 const C=root.SectionCore||(typeof require==='function'?require('./core.js'):null);
 const data=typeof document!=='undefined'?JSON.parse(document.getElementById('templateData').textContent):require('./sections.json');
 function validate(catalog=data){
  if(catalog?.format!=='section-studio-template-library'||catalog.schema!==1||catalog.units!=='ft'||!Array.isArray(catalog.templates))throw new Error('Неверный формат библиотеки сечений.');
  const ids=new Set();
  for(const t of catalog.templates){
   if(typeof t.id!=='string'||ids.has(t.id)||typeof t.name!=='string'||!['fuselage','gondola'].includes(t.role))throw new Error('Повторное или неверное имя шаблона.');ids.add(t.id);
   if(![t.widthFt,t.heightFt].every(v=>Number.isFinite(v)&&v>0&&v<1e5))throw new Error(t.name+': неверные размеры.');
   const s={x:1,y:0,z:0,width:t.widthFt,height:t.heightFt,shape:'custom',points:C.clone(t.points),knots:C.clone(t.knots),freeform:true,symmetric:t.symmetric};
   const p={format:'section-studio',schema:1,units:'ft',name:'Template',strength:.7,sections:[{...C.clone(s),x:0,width:0,height:0,shape:'point'},s,{...C.clone(s),x:2,width:0,height:0,shape:'point'}]};
   const errors=C.validateBody({...p,id:'fuselage',enabled:true});if(errors.length)throw new Error(t.name+': '+errors[0]);
  }
  return {templates:ids.size,models:new Set(catalog.templates.map(t=>t.model)).size};
 }
 function apply(section,template,mode='fit'){
  if(!['fit','source'].includes(mode))throw new Error('Выбери режим размеров шаблона.');
  if(!data.templates.some(t=>t.id===template.id))throw new Error('Шаблон не найден в библиотеке.');
  const s=C.clone(section);
  if(mode==='source'){s.width=template.widthFt;s.height=template.heightFt;}
  if(!(s.width>0&&s.height>0))throw new Error('Для точечной станции выбери исходные размеры шаблона.');
  Object.assign(s,{shape:'custom',points:C.clone(template.points),knots:C.clone(template.knots),freeform:true,symmetric:template.symmetric,
                  template:{id:template.id,name:template.name,model:template.model,libraryVersion:data.version,sizeMode:mode}});
  return s;
 }
 const api={data,validate,apply};if(typeof module!=='undefined'&&module.exports)module.exports=api;root.SectionTemplates=api;
})(typeof globalThis!=='undefined'?globalThis:this);
