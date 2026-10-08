"""Run under real pvpython. Synthetic scenes only; no new aerodynamic solve."""
from pathlib import Path
import json
from paraview.simple import *

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'outputs_release/synthetic_vspaero/manifest.json').read_text())
records = []
for record in manifest['points']:
    folder = (root / 'outputs_release/synthetic_vspaero' / record['file']).parent
    script = folder / 'scene.py'
    exec(compile(script.read_text(), str(script), 'exec'), {'__file__': str(script)})
    source = GetActiveSource()
    # Scene's final active source is the text label; inspect pressure reader explicitly.
    reader = next(proxy for proxy in GetSources().values() if 'Cp_or_DeltaCp' in proxy.CellData.keys())
    reader.UpdatePipeline()
    assert reader.GetDataInformation().GetNumberOfCells() == 1
    assert reader.CellData['Cp_or_DeltaCp'].GetRange() == (-0.125, -0.125)
    SaveScreenshot(str(folder / 'synthetic_preview.png'), GetActiveView(), ImageResolution=[800, 600])
    records.append({'file': str(script), 'cells': 1, 'cp_range': [-0.125, -0.125]})
    ResetSession()
script = root / 'outputs_release/synthetic_machline/scene.py'
exec(compile(script.read_text(), str(script), 'exec'), {'__file__': str(script)})
reader = next(proxy for proxy in GetSources().values() if 'C_p' in proxy.CellData.keys())
assert reader.CellData['C_p'].GetRange() == (-0.125, -0.125)
records.append({'file': str(script), 'cp_range': [-0.125, -0.125]})
(root / 'paraview_verification_release.json').write_text(json.dumps({'real_paraview': True, 'records': records}, indent=2))
print('ParaView scenes read and rendered without field changes')
