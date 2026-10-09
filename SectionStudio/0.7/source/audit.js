/* Live preliminary diagnostics of two bodies and mirrored copies. No aerodynamic solve. */
(function(root){
 'use strict';
 const severityOrder={error:0,warning:1,info:2};
 const clamp=x=>Math.max(-1,Math.min(1,x));
 function area(poly){let a=0;for(let i=0;i<poly.length;i++){const p=poly[i],q=poly[(i+1)%poly.length];a+=p[0]*q[1]-q[0]*p[1];}return Math.abs(a)*.5;}
 function pointDistance(p,a,b){const dx=b[0]-a[0],dy=b[1]-a[1],d=dx*dx+dy*dy,t=d?Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/d)):0;const x=a[0]+t*dx,y=a[1]+t*dy;return {distance:Math.hypot(p[0]-x,p[1]-y),point:[x,y]};}
 function inside(p,poly){let hit=false;for(let i=0,j=poly.length-1;i<poly.length;j=i++){const a=poly[i],b=poly[j];if((a[1]>p[1])!==(b[1]>p[1])&&p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0])hit=!hit;}return hit;}
 function strictInside(p,poly,eps){if(!inside(p,poly))return false;for(let i=0;i<poly.length;i++)if(pointDistance(p,poly[i],poly[(i+1)%poly.length]).distance<=eps)return false;return true;}
 function segmentHit(a,b,c,d,eps){const ux=b[0]-a[0],uy=b[1]-a[1],vx=d[0]-c[0],vy=d[1]-c[1],den=ux*vy-uy*vx;
  if(Math.abs(den)<1e-20)return null;const rx=c[0]-a[0],ry=c[1]-a[1],t=(rx*vy-ry*vx)/den,k=(rx*uy-ry*ux)/den;
  if(t<0||t>1||k<0||k>1)return null;const tol=eps/Math.max(eps,Math.hypot(ux,uy),Math.hypot(vx,vy));return {point:[a[0]+t*ux,a[1]+t*uy],proper:t>tol&&t<1-tol&&k>tol&&k<1-tol};
 }
 function polygonGap(a,b,eps){
  let distance=Infinity,pa=null,pb=null,crossing=false;
  for(let i=0;i<a.length;i++)for(let j=0;j<b.length;j++){
   const p=a[i],q=a[(i+1)%a.length],r=b[j],s=b[(j+1)%b.length];
   const hit=segmentHit(p,q,r,s,eps);if(hit){distance=0;pa=pb=hit.point;if(hit.proper)crossing=true;}
   if(distance===0)continue;
   const dx=Math.max(0,Math.min(p[0],q[0])-Math.max(r[0],s[0]),Math.min(r[0],s[0])-Math.max(p[0],q[0])),dy=Math.max(0,Math.min(p[1],q[1])-Math.max(r[1],s[1]),Math.min(r[1],s[1])-Math.max(p[1],q[1]));
   if(Math.hypot(dx,dy)>distance)continue;
   for(const [pt,lo,hi,reverse] of [[p,r,s,false],[q,r,s,false],[r,p,q,true],[s,p,q,true]]){const near=pointDistance(pt,lo,hi);if(near.distance<distance){distance=near.distance;pa=reverse?near.point:pt;pb=reverse?pt:near.point;}}
  }
  const ca=a.reduce((s,p)=>[s[0]+p[0]/a.length,s[1]+p[1]/a.length],[0,0]),cb=b.reduce((s,p)=>[s[0]+p[0]/b.length,s[1]+p[1]/b.length],[0,0]);
  const overlap=crossing||a.some(p=>strictInside(p,b,eps))||b.some(p=>strictInside(p,a,eps))||[ca,cb].some(p=>strictInside(p,a,eps)&&strictInside(p,b,eps));
  return {state:overlap?'overlap':distance<=eps?'touching':'separated',distance,a:pa,b:pb};
 }
 function selfCross(poly,eps){for(let i=0;i<poly.length;i++)for(let j=i+2;j<poly.length;j++){if(i===0&&j===poly.length-1)continue;const hit=segmentHit(poly[i],poly[(i+1)%poly.length],poly[j],poly[(j+1)%poly.length],eps);if(hit?.proper)return hit.point;}return null;}
 function checkGeometry(project,options={},meta={},C=root.SectionCore){
  const gapLimit=Number(options.minGap??.001),angleLimit=Number(options.angleLimit??65),contourSegments=96;
  if(!Number.isFinite(gapLimit)||gapLimit<0||gapLimit>10)throw new Error('Порог зазора должен быть от 0 до 10 ft.');
  if(!Number.isFinite(angleLimit)||angleLimit<5||angleLimit>89)throw new Error('Порог резкого изменения — от 5 до 89°.');
  const bodies=project.bodies,tilted=bodies.map(b=>b.sections.some(s=>Math.abs(s.pitch||0)>1e-8||Math.abs(s.yaw||0)>1e-8)),enabled=bodies.flatMap((b,i)=>b.enabled?[i]:[]),stations=[...new Set(enabled.flatMap(i=>bodies[i].sections.map(s=>s.x)))].sort((a,b)=>a-b),length=stations.at(-1)-stations[0],eps=Math.max(1e-9,length*1e-10);
  const xx=new Set(stations);for(let j=0;j<stations.length-1;j++){const a=stations[j],b=stations[j+1],n=Math.max(4,Math.ceil((b-a)/length*120));for(let k=1;k<n;k++)xx.add(a+(b-a)*k/n);}
  const allPlanes=[...xx].sort((a,b)=>a-b);
  const focused=Number.isFinite(options.focusX);let planes=allPlanes;
  if(focused)planes=[options.focusX].filter(v=>v>=stations[0]&&v<=stations.at(-1));
  const issues=[],groups=new Map();let minimumGap=Infinity,minimumGapX=null;
  const labels=['Фюзеляж','Мотогондола'];
  function add(code,severity,title,detail,action,x,body,point,index,extra={}){
   const key=code+':'+(extra.instancePair||body).join(','),prev=groups.get(key);
   if(prev&&index!==null&&prev.lastIndex===index-1){prev.x1=x;prev.count++;prev.lastIndex=index;if(extra.distance!==undefined&&(prev.distance===undefined||extra.distance<prev.distance)){prev.distance=extra.distance;prev.x=x;prev.point=point;}return;}
   const item={code,severity,title,detail,action,x,x0:x,x1:x,body,point,count:1,lastIndex:index,...extra};issues.push(item);groups.set(key,item);
  }
  planes.forEach((x,index)=>{
   const sections=bodies.map(b=>C.sectionAt(b,x)),polys=sections.map(a=>a?C.ring(a.section,contourSegments).slice(0,-1).map(p=>p.slice(1)):null);
   enabled.forEach(i=>{const s=sections[i]?.section,p=polys[i];if(!s||s.width===0||tilted[i])return;
    const hit=selfCross(p,eps);if(hit)add('self_intersection','error',labels[i]+': самопересечение контура','Контур пересекает себя на предварительной поверхности.','Исправь управляющие точки; проверь соседние плоскости.',x,[i],[x,...hit],index);
    if(area(p)<s.width*s.height*1e-5)add('collapsed_section','error',labels[i]+': почти нулевая площадь сечения','При ненулевых габаритах контур почти схлопнулся.','Восстанови форму сечения и проверь управляющие точки.',x,[i],[x,0,s.z],index);
    if(Math.min(s.width,s.height)<length*1e-5&&s.shape!=='point')add('small_section','warning',labels[i]+': очень малое сечение','Размер меньше 0,00001 общей длины модели.','Проверь, нужна ли эта узкая часть и достаточно ли разрешения сетки.',x,[i],[x,0,s.z],index);
   });
   const instances=enabled.flatMap(i=>C.ringsAt(bodies[i],x,contourSegments).map((r,copy)=>({body:i,copy,poly:r.slice(0,-1).map(p=>p.slice(1))})));
   for(let a=0;a<instances.length;a++)for(let b=a+1;b<instances.length;b++){
    const ia=instances[a],ib=instances[b];if(tilted[ia.body]||tilted[ib.body])continue;const pair=[...new Set([ia.body,ib.body])],g=polygonGap(ia.poly,ib.poly,eps),point=[x,...(g.a||[0,sections[ia.body].section.z])];
    if(g.state!=='separated'){if(minimumGap!==0)minimumGapX=x;minimumGap=0;}else if(g.distance<minimumGap){minimumGap=g.distance;minimumGapX=x;}
    const instancePair=[ia,ib].map(a=>a.body+':'+a.copy),extra={distance:g.distance,instancePair},names=[ia,ib].map(a=>bodies[a.body].name+(a.copy?' (зеркальная копия)':'')).join(' и ');
    if(g.state==='overlap')add('body_overlap','error',pair.length===1?'Пересечение с зеркальной копией':'Пересечение двух тел',names+': области перекрываются на выборке.','Для раздельных тел устрани перекрытие. Если стык задуман, нужна согласованная сетка после объединения в OpenVSP.',x,pair,point,index,{...extra,distance:0});
    else if(g.state==='touching')add('body_contact','warning','Контакт тел или зеркальных копий',names+': положительный зазор не обнаружен.','Задай контролируемый зазор либо сформируй общий согласованный стык.',x,pair,point,index,extra);
    else if(g.distance<gapLimit)add('small_gap','warning','Зазор меньше выбранного порога',names+`: зазор меньше ${gapLimit} ft. Порог задаётся пользователем и не является допуском решателя.`,'Увеличь зазор или уточни местную сетку, затем проверь TRI.',x,pair,point,index,extra);
   }
  });
  enabled.forEach(i=>{const b=bodies[i],ss=b.sections;
   for(let k=0;k<ss.length-1;k++){const a=ss[k],s=ss[k+1];if(!a.width||!s.width)continue;const dx=s.x-a.x,delta=Math.max(Math.abs(s.width-a.width)/2,Math.abs(s.z+s.height/2-a.z-a.height/2),Math.abs(s.z-s.height/2-a.z+a.height/2)),angle=Math.atan2(delta,dx)*180/Math.PI;
    if(angle>angleLimit){const x=(a.x+s.x)/2;add('rapid_change','warning',labels[i]+': резкое изменение между сечениями',`Средний наклон изменения размера или положения ≈ ${angle.toFixed(1)}°. Это признак для осмотра, а не ограничение OpenVSP.`,'Разнеси станции или смягчи переход; проверь локальные треугольники.',x,[i],[x,0,(a.z+s.z)/2],null,{x0:a.x,x1:s.x,angle});}
    if(dx<.02*Math.min(Math.max(a.width,a.height),Math.max(s.width,s.height))){const x=(a.x+s.x)/2;add('close_stations','warning',labels[i]+': близко расположенные станции','Продольный интервал меньше 2% местного поперечного габарита. Возможны очень короткие панели при сгущении сетки.','Проверь, нужен ли такой короткий переход, и качество будущей сетки.',x,[i],[x,0,(a.z+s.z)/2],null,{x0:a.x,x1:s.x});}
   }
   for(const [side,k] of [['front',0],['rear',ss.length-1]])if(!meta.engineModes?.[i]&&ss[k].width>0&&meta.caps?.[i]?.[side]===0){const s=ss[k];add('open_end','error',labels[i]+': торец без заглушки','В исходном VSP3 выбран NO_END_CAP при ненулевом конечном сечении.','Для замкнутого внешнего тела добавь заглушку в OpenVSP и заново построй сетку.',s.x,[i],[s.x,s.y||0,s.z],null);}
  });
  issues.sort((a,b)=>severityOrder[a.severity]-severityOrder[b.severity]||a.x-b.x);issues.forEach((i,n)=>{delete i.lastIndex;i.id='geometry-'+n;});
  return {kind:'geometry',scope:focused?'local':'full',focusX:focused?options.focusX:null,issues,summary:{bodies:enabled.length,instances:enabled.reduce((n,i)=>n+(bodies[i].mirrorY?2:1),0),tiltedBodiesSkipped:enabled.filter(i=>tilted[i]).length,engineModesIgnored:enabled.filter(i=>meta.engineModes?.[i]).length,planes:planes.length,contourSegments,minGap:Number.isFinite(minimumGap)?minimumGap:null,minGapX:minimumGapX,errors:issues.filter(i=>i.severity==='error').length,warnings:issues.filter(i=>i.severity==='warning').length,otherComponents:meta.otherComponents||0},settings:{minGap:gapLimit,angleLimit},limits:['Проверены только выбранные тела и их поддерживаемые зеркальные копии, по предварительным базовым сечениям редактора.','Узкие зоны между плоскостями и между отсчётами контура могут быть пропущены.','Проверка не воспроизводит Skinning OpenVSP и не запускает расчёт.','Двигательные обрезки, проточные каналы и их торцы не проверяются.','Для тел с наклоном станций по Y/Z пространственные пересечения и зазор не проверяются; показана проекция.']};
 }
 const api={checkGeometry,polygonGap,selfCross};if(typeof module!=='undefined'&&module.exports)module.exports=api;root.SectionAudit=api;
})(typeof globalThis!=='undefined'?globalThis:this);
