/* Section Studio 0.7 — two editable bodies in shared world coordinates, feet. */
(function (root) {
  'use strict';
  const VERSION = '0.7.0';
  const clone = o => JSON.parse(JSON.stringify(o));
  const CHINE = [[.5,.22],[.44,.08],[.34,-.40],[.29,-.46],[.25,-.5],[.12,-.5],[0,-.5],[-.12,-.5],[-.25,-.5],[-.29,-.46],[-.34,-.40],[-.44,.08],[-.5,.22],[-.475,.32],[-.445,.47],[-.40,.5],[-.27,.5],[-.13,.5],[0,.5],[.13,.5],[.27,.5],[.40,.5],[.445,.47],[.475,.32],[.5,.22]];
  function preset(kind) {
    if (kind === 'chine') return clone(CHINE);
    if (kind === 'trapezoid') {
      const a = [[.5,-.30],[.5,-.44],[.47,-.5],[.40,-.5],[.28,-.5],[.14,-.5],[0,-.5],[-.14,-.5],[-.28,-.5],[-.40,-.5],[-.47,-.5],[-.5,-.44],[-.5,-.30],[-.46,-.02],[-.34,.40],[-.28,.5],[-.19,.5],[-.09,.5],[0,.5],[.09,.5],[.19,.5],[.28,.5],[.34,.40],[.46,-.02],[.5,-.30]];
      return a;
    }
    const p = [], step = -Math.PI / 4, k = 4 / 3 * Math.tan(step / 4);
    for (let i = 0; i < 8; i++) {
      const a = i * step, b = (i + 1) * step;
      p.push([.5 * Math.cos(a), .5 * Math.sin(a)],
        [.5 * (Math.cos(a) - k * Math.sin(a)), .5 * (Math.sin(a) + k * Math.cos(a))],
        [.5 * (Math.cos(b) + k * Math.sin(b)), .5 * (Math.sin(b) - k * Math.cos(b))]);
    }
    p.push([.5, 0]);
    return p.map(q => q.map(v => Math.abs(v) < 1e-14 ? 0 : v));
  }
  function demo() {
    return {format: 'section-studio', schema: 1, units: 'ft', name: 'Fuselage', strength: .7,
      sections: [[0,0,0,0,'point'],[3,2.4,1.6,.20,'ellipse'],[7,4.4,2.7,.45,'chine'],[12,5,3,.55,'chine'],[18,4.8,2.8,.65,'chine'],[24,3.4,1.7,.9,'chine'],[28,1.8,.8,1.10,'ellipse'],[28.3,0,0,1.10,'point']]
        .map(([x,width,height,z,shape])=>({x,width,height,z,shape,points:preset(shape)}))};
  }
  function curve(points, t, knots) {
    const n=(points.length-1)/3;
    let j=0,u=0;
    if(knots){t=Math.max(0,Math.min(1,t));while(j<n-1&&t>knots[3*j+3])j++;u=(t-knots[3*j])/(knots[3*j+3]-knots[3*j]);}
    else {const q=Math.min(n-1e-12,Math.max(0,t)*n);j=Math.floor(q);u=q-j;}
    const a = 1-u;
    return [0,1].map(d => a*a*a*points[3*j][d]+3*a*a*u*points[3*j+1][d]+3*a*u*u*points[3*j+2][d]+u*u*u*points[3*j+3][d]);
  }
  function ring(s, n=96) {
    return Array.from({length:n+1},(_,i)=>{
      const p = curve(s.points,i/n,s.knots);
      return point3(s,p);
    });
  }
  // OpenVSP applies section rotations in Z, Y, X order before translation.
  function rotation(s){const rx=(s.roll||0)*Math.PI/180,ry=(s.pitch||0)*Math.PI/180,rz=(s.yaw||0)*Math.PI/180,cx=Math.cos(rx),sx=Math.sin(rx),cy=Math.cos(ry),sy=Math.sin(ry),cz=Math.cos(rz),sz=Math.sin(rz);return {a:-cy*sz,b:sy,c:cx*cz-sx*sy*sz,d:-sx*cy,e:sx*cz+cx*sy*sz,f:cx*cy};}
  function point3(s,p){const r=rotation(s),y=s.width*p[0],z=s.height*p[1];return [s.x+r.a*y+r.b*z,(s.y||0)+r.c*y+r.d*z,s.z+r.e*y+r.f*z];}
  function localPoint(s,y,z){const r=rotation(s),det=r.c*r.f-r.d*r.e;if(Math.abs(det)<1e-6||!s.width||!s.height)return null;y-=s.y||0;z-=s.z;return [(r.f*y-r.d*z)/det/s.width,(-r.e*y+r.c*z)/det/s.height];}
  function slopes(x, y) {
    const n=x.length, h=x.slice(1).map((v,i)=>v-x[i]), d=h.map((v,i)=>(y[i+1]-y[i])/v), m=new Array(n).fill(0);
    if(n===2)return [d[0],d[0]];
    for(let i=1;i<n-1;i++)if(d[i-1]*d[i]>0){const a=2*h[i]+h[i-1],b=h[i]+2*h[i-1];m[i]=(a+b)/(a/d[i-1]+b/d[i]);}
    function end(h0,h1,d0,d1){let v=((2*h0+h1)*d0-h0*d1)/(h0+h1);if(v*d0<=0)return 0;if(d0*d1<0&&Math.abs(v)>3*Math.abs(d0))v=3*d0;return v;}
    m[0]=end(h[0],h[1],d[0],d[1]);m[n-1]=end(h[n-2],h[n-3],d[n-2],d[n-3]);return m;
  }
  function interpolator(x,y){const m=slopes(x,y);return q=>{let i=0;while(i<x.length-2&&q>x[i+1])i++;const h=x[i+1]-x[i],t=Math.max(0,Math.min(1,(q-x[i])/h));return (2*t*t*t-3*t*t+1)*y[i]+(t*t*t-2*t*t+t)*h*m[i]+(-2*t*t*t+3*t*t)*y[i+1]+(t*t*t-t*t)*h*m[i+1];};}
  function loft(project, n=72, nw=64) {
    const s=project.sections,x=s.map(p=>p.x), rings=s.map(p=>ring(p,nw)), rows=[];
    const f=Array.from({length:nw+1},(_,j)=>[1,2].map(k=>interpolator(x,rings.map(r=>r[j][k]))));
    for(let i=0;i<=n;i++){const xx=x[0]+i/n*(x[x.length-1]-x[0]);rows.push(f.map(q=>[xx,q[0](xx),q[1](xx)]));}
    return rows;
  }
  function intersect(a,b,c,d){
    const cross=(p,q,r)=>(q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0]);
    return cross(a,b,c)*cross(a,b,d)<-1e-12&&cross(c,d,a)*cross(c,d,b)<-1e-12;
  }
  function validate(p, thorough=true) {
    const e=[];
    if(!p||p.format!=='section-studio'||p.schema!==1||p.units!=='ft')return ['Нужен проект Section Studio версии 1, в футах.'];
    if(typeof p.name!=='string'||! /^[\p{L}][\p{L}\p{N}_ .-]{0,79}$/u.test(p.name))e.push('Имя компонента: буквы, цифры, пробелы, _, точка и дефис; начиная с буквы.');
    if(!Number.isFinite(p.strength)||p.strength<0||p.strength>1)e.push('Сглаживание должно быть от 0 до 1.');
    if(!Array.isArray(p.sections)||p.sections.length<3||p.sections.length>64)return e.concat('Нужно от 3 до 64 сечений.');
    p.sections.forEach((s,i)=>{
      const tag=`Сечение ${i+1}: `;
      if(!["point","ellipse","chine","trapezoid","custom"].includes(s.shape))e.push(tag+"неизвестная форма.");
      if(!['x','width','height','z'].every(k=>Number.isFinite(s[k])&&Math.abs(s[k])<=1e5)){e.push(tag+'недопустимое число.');return;}
      if(i&&s.x-p.sections[i-1].x<.001)e.push(tag+'станции X должны возрастать минимум на 0,001 ft.');
      if(s.width<0||s.height<0)e.push(tag+'размеры не могут быть отрицательными.');
      const point=s.width===0&&s.height===0;
      if(point!==(s.shape==="point"))e.push(tag+"форма «Точка» должна иметь нулевые размеры.");
      if((s.width===0)!==(s.height===0))e.push(tag+'для точки оба размера должны быть нулевыми.');
      if(point&&i!==0&&i!==p.sections.length-1)e.push(tag+'нулевое сечение допустимо только на торце.');
      if(s.y!==undefined&&(!Number.isFinite(s.y)||Math.abs(s.y)>1e5))e.push(tag+'недопустимая координата Y.');
      for(const k of ['roll','pitch','yaw'])if(s[k]!==undefined&&(!Number.isFinite(s[k])||Math.abs(s[k])>360))e.push(tag+'недопустимый поворот сечения.');
      if(s.freeform!==undefined&&typeof s.freeform!=='boolean')e.push(tag+'неверный режим контура.');
      if(s.symmetric!==undefined&&typeof s.symmetric!=='boolean')e.push(tag+'неверный режим симметрии.');
      if(!Array.isArray(s.points)||s.points.length<(s.freeform?7:13)||s.points.length>385||(s.points.length-1)%3||!s.points.every(q=>Array.isArray(q)&&q.length===2&&q.every(v=>Number.isFinite(v)&&Math.abs(v)<=(s.freeform?1e4:.501)))){e.push(tag+'неверные точки кубической кривой.');return;}
      const uu=knotsOf(s),close=(a,b)=>Math.abs(a-b)<1e-5;
      if(uu.length!==s.points.length||uu.some((v,j)=>!Number.isFinite(v)||(j&&v<=uu[j-1]))||Math.abs(uu[0])>1e-8||Math.abs(uu.at(-1)-1)>1e-8){e.push(tag+'неверная параметризация кривой.');return;}
      const bottom=uu.findIndex(v=>Math.abs(v-.25)<1e-8),top=uu.findIndex(v=>Math.abs(v-.75)<1e-8);
      if(s.freeform){if(!close(s.points[0][0],s.points.at(-1)[0])||!close(s.points[0][1],s.points.at(-1)[1]))e.push(tag+'контур должен быть замкнут.');}
      else if(bottom<0||top<0||!close(s.points[0][0],.5)||!close(s.points.at(-1)[0],.5)||!close(s.points[0][1],s.points.at(-1)[1])||!close(s.points[bottom][0],0)||!close(s.points[bottom][1],-.5)||!close(s.points[top][0],0)||!close(s.points[top][1],.5))e.push(tag+'нарушены размерные точки или замыкание кривой.');
      if(!s.freeform||s.symmetric)for(let k=0;k<s.points.length-1;k++){const j=mirrorIndex(s,k);if(j<0||!close(s.points[k][0],-s.points[j][0])||!close(s.points[k][1],s.points[j][1])){e.push(tag+'нарушена симметрия контура.');break;}}
      if(thorough&&!point){const r=Array.from({length:128},(_,k)=>curve(s.points,k/128,s.knots));let hit=false;for(let a=0;a<128&&!hit;a++)for(let b=a+2;b<128;b++){if(a===0&&b===127)continue;if(intersect(r[a],r[(a+1)%128],r[b],r[(b+1)%128])){hit=true;break;}}if(hit)e.push(tag+'контур пересекает сам себя.');}
    });return e;
  }
  function knotsOf(s){return s.knots||s.points.map((_,i)=>i/(s.points.length-1));}
  function mirrorIndex(s,k){const u=knotsOf(s),target=(1.5-u[k])%1;return u.findIndex(v=>Math.abs(v-target)<1e-7);}
  function editable(s){const u=knotsOf(s);return u.flatMap((v,i)=>i<s.points.length-1&&(s.freeform&&!s.symmetric||v<.25-1e-8||v>.75+1e-8)?[i]:[]);}
  function refine(s,breaks){
    const u=knotsOf(s),p=s.points,out=[],knots=[];
    const mix=(a,b,t)=>a.map((v,k)=>(1-t)*v+t*b[k]);
    function split(q,t){const a=mix(q[0],q[1],t),b=mix(q[1],q[2],t),c=mix(q[2],q[3],t),d=mix(a,b,t),e=mix(b,c,t),f=mix(d,e,t);return [[q[0],a,d,f],[f,e,c,q[3]]];}
    for(let k=0;k<breaks.length-1;k++){
      const a=breaks[k],b=breaks[k+1];let j=0;while(j<(p.length-4)/3&&a>=u[3*j+3]-1e-9)j++;
      const lo=u[3*j],hi=u[3*j+3];let q=p.slice(3*j,3*j+4);if(b<hi-1e-10)q=split(q,(b-lo)/(hi-lo))[0];if(a>lo+1e-10)q=split(q,(a-lo)/(b-lo))[1];
      out.push(...q.slice(0,3));knots.push(a,a+(b-a)/3,a+2*(b-a)/3);
    }
    out.push(s.points.at(-1).slice());knots.push(1);return {points:out,knots};
  }
  function movePoint(s,index,y,z){
    if(index===s.points.length-1)index=0;if(!editable(s).includes(index))return;
    if(s.freeform){y=Math.max(-1e4,Math.min(1e4,y));z=Math.max(-1e4,Math.min(1e4,z));}
    else{y=Math.max(0,Math.min(.5,y));z=Math.max(-.5,Math.min(.5,z));if(index===0)y=.5;}
    s.points[index]=[y,z];if(!s.freeform||s.symmetric){const m=mirrorIndex(s,index);if(m>=0)s.points[m]=[-y,z];}s.points[s.points.length-1]=s.points[0].slice();s.shape='custom';
  }
  function exportScript(p) {
    const errors=validate(p);if(errors.length)throw new Error(errors.join('\n'));
    const s=p.sections,x=s.map(q=>q.x),L=x.at(-1)-x[0];
    const top=slopes(x,s.map(q=>q.z+q.height/2)),bottom=slopes(x,s.map(q=>q.z-q.height/2)),side=slopes(x,s.map(q=>q.width/2));
    const f=v=>Number(v).toPrecision(15), lines=[
      '// Section Studio '+VERSION+' | units: feet | new editable FUSELAGE component',
      '// Adds a component; does not clear an existing OpenVSP model.',
      'void main() {',`  string g=AddGeom("FUSELAGE", "");`,
      `  SetGeomName(g, "${p.name}");`, `  SetParmVal(g, "Length", "Design", ${f(L)});`,
      `  SetParmVal(g, "X_Location", "XForm", ${f(x[0])});`,
      `  SetParmVal(g, "X_Rel_Location", "XForm", ${f(x[0])});`,
      '  string surf=GetXSecSurf(g, 0);',
      `  while(GetNumXSec(surf)>${s.length}) { CutXSec(g, 1); Update(); }`,
      `  while(GetNumXSec(surf)<${s.length}) { InsertXSec(g, GetNumXSec(surf)-2, XS_EDIT_CURVE); Update(); }`,
      '  array<double> positions = {'+s.map(q=>f((q.x-x[0])/L)).join(',')+'};',
      '  // Repeated ordered passes respect OpenVSP neighbour bounds.',
      `  for(int pass=0; pass<${s.length+1}; pass++) {`,
      `    for(int i=0;i<${s.length};i++) { SetParmVal(GetXSecParm(GetXSec(surf,i), "XLocPercent"), positions[i]); Update(); }`,
      '  }', '  string xs;', '  array<vec3d> p(25);', '  array<double> u(25);', '  array<double> r(25);',
      '  for(int k=0;k<25;k++) {u[k]=double(k)/24.0; r[k]=0.0;}'
    ];
    s.forEach((q,i)=>{
      const point=q.width===0;
      lines.push(`  ChangeXSecShape(surf, ${i}, ${point?'XS_POINT':'XS_EDIT_CURVE'});`,`  xs=GetXSec(surf, ${i});`,'  ResetXSecSkinParms(xs);',`  SetParmVal(GetXSecParm(xs, "ZLocPercent"), ${f(q.z/L)});`);
      if(!point){lines.push('  SetParmVal(GetXSecParm(xs, "SymType"), SYM_NONE);','  EditXSecConvertTo(xs, CEDIT);',`  SetXSecWidthHeight(xs, ${f(q.width)}, ${f(q.height)});`,`  p.resize(${q.points.length}); u.resize(${q.points.length}); r.resize(${q.points.length});`);q.points.forEach((pt,j)=>lines.push(`  p[${j}]=vec3d(${f(pt[0])}, ${f(pt[1])}, 0); u[${j}]=${f(knotsOf(q)[j])}; r[${j}]=0;`));lines.push('  SetEditXSecPnts(xs,u,p,r);');}
      const a=[Math.atan(top[i]),Math.atan(side[i]),-Math.atan(bottom[i]),Math.atan(side[i])].map(v=>f(v*180/Math.PI));
      lines.push('  SetXSecContinuity(xs, 1);',`  SetXSecTanAngles(xs,XSEC_BOTH_SIDES,${a.join(',')});`,`  SetXSecTanStrengths(xs,XSEC_BOTH_SIDES,${Array(4).fill(f(p.strength)).join(',')});`,'  SetParmVal(GetXSecParm(xs,"SectTess_U"),12);');
    });
    lines.push(`  SetParmVal(g,"CapUMinOption","EndCap",${s[0].width===0?'NO_END_CAP':'FLAT_END_CAP'});`,`  SetParmVal(g,"CapUMaxOption","EndCap",${s.at(-1).width===0?'NO_END_CAP':'FLAT_END_CAP'});`,'  SetParmVal(g,"Tess_W","Shape",65);','  Update();',
      '  bool folded=false;',
      '  for(int j=0;j<=48;j++) {',
      '    vec3d prev=CompPnt01(g,0,0.0,double(j)/48.0);',
      '    for(int i=1;i<=240;i++) { vec3d pt=CompPnt01(g,0,double(i)/240.0,double(j)/48.0); if(pt.x()<prev.x()-1e-7) folded=true; prev=pt; }',
      '  }',
      '  if(folded) Print("SECTION_STUDIO_WARNING: Surface folds along X. Reduce skin strength or increase station spacing; inspect the VSP3 before use.");',
      '  bool failed=false;', '  while(GetNumTotalErrors()>0) { ErrorObj e=PopLastError(); Print(e.GetErrorString()); failed=true; }','  if(failed) { Print("SECTION_STUDIO_ERROR"); return; }',
      `  string output_file="${p.name}_sections_"+g+".vsp3";`, '  WriteVSPFile(output_file,SET_ALL);', '  if(GetNumTotalErrors()>0) { while(GetNumTotalErrors()>0) { ErrorObj e=PopLastError(); Print(e.GetErrorString()); } Print("SECTION_STUDIO_ERROR"); return; }', '  Print("SECTION_STUDIO_SAVED: "+output_file);', '}');
    return lines.join('\n')+'\n';
  }
  const bodyProject=b=>({format:'section-studio',schema:1,units:'ft',name:b.name,strength:b.strength,sections:b.sections});
  function demoAssembly(){
    const f=demo();
    const g=[[8,.8,.5,2.55],[10,1.9,1.15,2.74],[12,2.4,1.6,2.89],[18,2.35,1.5,2.87],[23,1.7,1.1,2.65],[26,.9,.6,2.30]];
    return {format:'section-studio',schema:2,units:'ft',name:'Assembly',bodies:[
      {id:'fuselage',name:'Fuselage',enabled:true,strength:f.strength,sections:f.sections},
      {id:'gondola',name:'Gondola',enabled:true,strength:.5,sections:g.map(([x,width,height,z])=>({x,width,height,z,shape:'trapezoid',points:preset('trapezoid')}))}
    ]};
  }
  function validateAssembly(p,thorough=true){
    if(!p||p.format!=='section-studio'||p.schema!==2||p.units!=='ft')return ['Нужен проект Section Studio, схема 2, в футах.'];
    const e=[];
    if(typeof p.name!=='string'||!/^[A-Za-z][A-Za-z0-9_]{0,39}$/.test(p.name))e.push('Имя проекта: латинские буквы, цифры и _, начиная с буквы.');
    if(!Array.isArray(p.bodies)||p.bodies.length!==2)return e.concat('Проект должен содержать фюзеляж и мотогондолу.');
    p.bodies.forEach((b,i)=>{
      if(!b||b.id!==['fuselage','gondola'][i]){e.push('Неверный порядок компонентов.');return;}
      if(typeof b.enabled!=='boolean')e.push('Неверный признак включения компонента.');
      if(b.mirrorY!==undefined&&typeof b.mirrorY!=='boolean')e.push('Неверный признак зеркальной копии.');
      e.push(...validate(bodyProject(b),thorough).map(t=>(i?'Мотогондола: ':'Фюзеляж: ')+t));
    });
    if(!p.bodies[0]?.enabled)e.push('Фюзеляж должен быть включён.');
    if(p.bodies[0]?.name===p.bodies[1]?.name)e.push('Компонентам нужны разные имена.');
    return e;
  }
  function importProject(p,thorough=true){
    if(p?.schema===1){const e=validate(p,thorough);if(e.length)throw new Error(e.join('\n'));const q=demoAssembly();q.name=p.name+'_pair';if(q.name.length>40)q.name='Assembly';q.bodies[0]={id:'fuselage',enabled:true,name:p.name,strength:p.strength,sections:clone(p.sections)};q.bodies[1].enabled=false;if(q.bodies[1].name===p.name)q.bodies[1].name='Gondola_2';return q;}
    const e=validateAssembly(p,thorough);if(e.length)throw new Error(e.join('\n'));return clone(p);
  }
  function sectionAt(b,x){
    const ss=b.sections;
    if(!b.enabled||x<ss[0].x-1e-8||x>ss.at(-1).x+1e-8)return null;
    const exact=ss.findIndex(s=>Math.abs(s.x-x)<1e-8);
    if(exact>=0)return {section:ss[exact],index:exact};
    let j=0;while(j<ss.length-2&&x>ss[j+1].x)j++;
    const t=(x-ss[j].x)/(ss[j+1].x-ss[j].x),xx=ss.map(s=>s.x),s={x,shape:'custom'};
    for(const k of ['width','height','z'])s[k]=interpolator(xx,ss.map(s=>s[k]))(x);
    if(ss.some(s=>s.y!==undefined))s.y=interpolator(xx,ss.map(s=>s.y||0))(x);
    for(const k of ['roll','pitch','yaw'])if(ss.some(s=>s[k]!==undefined))s[k]=interpolator(xx,ss.map(s=>s[k]||0))(x);
    if(ss[j].freeform||ss[j+1].freeform){s.freeform=true;s.symmetric=(!ss[j].freeform||!!ss[j].symmetric)&&(!ss[j+1].freeform||!!ss[j+1].symmetric);}
    const breaks=[...new Set([ss[j],ss[j+1]].flatMap(s=>knotsOf(s).filter((_,i)=>i%3===0)).map(v=>Number(v.toFixed(10))))].sort((a,b)=>a-b);
    const aa=refine(ss[j],breaks),bb=refine(ss[j+1],breaks);s.points=aa.points.map((q,i)=>q.map((v,k)=>(1-t)*v+t*bb.points[i][k]));s.knots=aa.knots;
    return {section:s,index:-1};
  }
  function addAt(b,x){
    const a=sectionAt(b,x);
    if(!a)throw new Error('Плоскость X находится вне длины компонента.');
    if(a.index>=0)return a.index;
    if(b.sections.length>=64)throw new Error('Предел: 64 сечения на компонент.');
    if(b.sections.some(s=>Math.abs(s.x-x)<.001-1e-10))throw new Error('До соседнего сечения нужно минимум 0,001 ft.');
    const i=b.sections.findIndex(s=>s.x>x);b.sections.splice(i,0,clone(a.section));return i;
  }
  function loftBody(b,n=64,nw=48){
    if(!b.enabled)return [];
    const x0=b.sections[0].x,L=b.sections.at(-1).x-x0;
    // Include prescribed stations exactly in the displayed mesh.
    const xx=[...new Set([...Array.from({length:n+1},(_,i)=>x0+i/n*L),...b.sections.map(s=>s.x)])].sort((a,b)=>a-b);
    return xx.map(x=>ring(sectionAt(b,x).section,nw));
  }
  function nearest(p,a,b){const dx=b[0]-a[0],dy=b[1]-a[1],den=dx*dx+dy*dy,t=den?Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/den)):0,q=[a[0]+t*dx,a[1]+t*dy];return {d:Math.hypot(p[0]-q[0],p[1]-q[1]),p:q};}
  function inside(p,poly){let yes=false;for(let i=0,j=poly.length-1;i<poly.length;j=i++){const a=poly[i],b=poly[j];if((a[1]>p[1])!==(b[1]>p[1])&&p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0])yes=!yes;}return yes;}
  function boundaryDistance(p,poly){let d=Infinity;for(let i=0;i<poly.length;i++)d=Math.min(d,nearest(p,poly[i],poly[(i+1)%poly.length]).d);return d;}
  function contourGap(a,b,n=160){
    const pa=ring(a,n).slice(0,-1).map(p=>p.slice(1)),pb=ring(b,n).slice(0,-1).map(p=>p.slice(1));
    let best={distance:Infinity,a:null,b:null},crossed=false;
    const cross=(u,v)=>u[0]*v[1]-u[1]*v[0],sub=(u,v)=>[u[0]-v[0],u[1]-v[1]];
    for(let i=0;i<n;i++)for(let j=0;j<n;j++){
      const p=pa[i],q=pa[(i+1)%n],r=pb[j],s=pb[(j+1)%n],u=sub(q,p),v=sub(s,r),den=cross(u,v);
      if(Math.abs(den)>1e-16){const t=cross(sub(r,p),v)/den,k=cross(sub(r,p),u)/den;
        if(t>=0&&t<=1&&k>=0&&k<=1){const hit=[p[0]+u[0]*t,p[1]+u[1]*t];best={distance:0,a:hit,b:hit};if(t>1e-9&&t<1-1e-9&&k>1e-9&&k<1-1e-9)crossed=true;}
      }
      for(const [pt,lo,hi,rev] of [[p,r,s,false],[q,r,s,false],[r,p,q,true],[s,p,q,true]]){const k=nearest(pt,lo,hi);if(k.d<best.distance)best={distance:k.d,a:rev?k.p:pt,b:rev?pt:k.p};}
    }
    const strict=(p,poly)=>inside(p,poly)&&boundaryDistance(p,poly)>1e-7;
    const center=p=>[0,1].map(k=>p.reduce((s,q)=>s+q[k],0)/p.length);
    const overlap=crossed||pa.some(p=>strict(p,pb))||pb.some(p=>strict(p,pa))||[center(pa),center(pb)].some(p=>strict(p,pa)&&strict(p,pb));
    return {...best,state:overlap?'overlap':best.distance<1e-7?'touching':'separated',samples:n};
  }
  function gapAt(p,x,n=160){const a=sectionAt(p.bodies[0],x),b=sectionAt(p.bodies[1],x);if(!a||!b)return {state:'unavailable'};return {...contourGap(a.section,b.section,n),exact:a.index>=0&&b.index>=0};}
  function ringsAt(b,x,n=96){const a=sectionAt(b,x);if(!a)return [];const r=ring(a.section,n);return b.mirrorY?[r,r.map(p=>[p[0],-p[1],p[2]])]:[r];}
  function exportAssembly(p){
    const e=validateAssembly(p);if(e.length)throw new Error(e.join('\n'));
    if(p.bodies.some(b=>b.enabled&&(b.mirrorY||b.sections.some(s=>s.y||s.roll||s.pitch||s.yaw))))throw new Error('Для зеркальных копий, поворотов и боковых смещений используй «Сохранить VSP3».');
    const bs=p.bodies.filter(b=>b.enabled),parts=['// Section Studio '+VERSION+' | two-body project | feet'];
    bs.forEach((b,i)=>{
      const base=exportScript(bodyProject(b));
      parts.push(base.slice(0,base.indexOf('  bool failed=false;')).replace('void main() {',`string buildBody${i}() {`).replace('SECTION_STUDIO_WARNING: Surface',`SECTION_STUDIO_WARNING: ${b.name}: Surface`)+'  return g;\n}');
    });
    parts.push('void main() {');bs.forEach((b,i)=>parts.push(`  string g${i}=buildBody${i}();`));
    parts.push('  bool failed=false;','  while(GetNumTotalErrors()>0) { ErrorObj e=PopLastError(); Print(e.GetErrorString()); failed=true; }','  if(failed) { Print("SECTION_STUDIO_ERROR"); return; }',`  string output_file="${p.name}_sections_"+g0+".vsp3";`,'  WriteVSPFile(output_file,SET_ALL);','  if(GetNumTotalErrors()>0) { while(GetNumTotalErrors()>0) { ErrorObj e=PopLastError(); Print(e.GetErrorString()); } Print("SECTION_STUDIO_ERROR"); return; }','  Print("SECTION_STUDIO_SAVED: "+output_file);','}');
    return parts.join('\n')+'\n';
  }
  const api={VERSION,clone,preset,demo:demoAssembly,legacyDemo:demo,curve,ring,point3,localPoint,ringsAt,slopes,interpolator,loft:loftBody,validate:validateAssembly,validateBody:(b,thorough=true)=>validate(bodyProject(b),thorough),movePoint,exportScript:exportAssembly,importProject,sectionAt,addAt,contourGap,gapAt,knotsOf,editable,refine};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;root.SectionCore=api;
})(typeof globalThis!=='undefined'?globalThis:this);
