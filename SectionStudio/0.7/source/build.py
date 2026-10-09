from pathlib import Path
p=Path(__file__).resolve().parent
s=(p/'template.html').read_text(encoding='utf-8').replace('/*CORE*/',(p/'core.js').read_text(encoding='utf-8')).replace('/*VSP*/',(p/'vsp.js').read_text(encoding='utf-8')).replace('/*AUDIT*/',(p/'audit.js').read_text(encoding='utf-8')).replace('/*TEMPLATE_DATA*/',(p/'sections.json').read_text(encoding='utf-8').replace('</','<\\/')).replace('/*TEMPLATES*/',(p/'templates.js').read_text(encoding='utf-8')).replace('/*APP*/',(p/'app.js').read_text(encoding='utf-8'))
(p.parent/'Section_Studio.html').write_text(s,encoding='utf-8')
print('Built Section_Studio.html')
