from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from vspgeom_integrity import validate_vspgeom


def mesh(points=None, faces=None, triangles=None, tags=None, wakes=None):
    points = points or [(0,0,0), (1,0,0), (1,1,0), (0,1,0)]
    faces = faces or [[1,2,3,4]]
    triangles = triangles if triangles is not None else [[[1,2,3], [1,3,4]]]
    tags = tags or [(1,1)] * len(faces)
    wakes = wakes or []
    rows = ['# vspgeom v3', '1', f'{len(points)} {len(faces)} {len(wakes)}']
    rows += [' '.join(map(str,p)) for p in points]
    rows += [str(len(faces))]
    rows += [' '.join(map(str,[len(f), *f])) for f in faces]
    rows += [' '.join(map(str,[*tag, *([0.] * (2 * len(f)))])) for tag,f in zip(tags,faces)]
    rows += [f'{i} {i}' for i in range(1,len(faces)+1)]
    rows += [str(len(wakes))]
    rows += [' '.join(map(str,[len(w), 0, *w])) for w in wakes]
    rows += [' '.join(map(str,[i,len(ts),*[n for t in ts for n in t]])) for i,ts in enumerate(triangles,1)]
    rows += [' '.join(map(str,[i,*tag,*([0.] * (6 * len(ts)))]))
             for i,(tag,ts) in enumerate(zip(tags,triangles),1)]
    return '\n'.join(rows)+'\n'


class IntegrityTests(unittest.TestCase):
    def check(self, data, expected, thick=()):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'model.vspgeom'
            path.write_text(data,encoding='utf-8')
            before=path.read_bytes()
            result=validate_vspgeom(path,thick_surface_ids=thick)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(result['sha256'],sha256(before).hexdigest())
        self.assertEqual(result['valid'],expected,result)
        self.assertFalse(result['qualification_granted'])
        return result

    def test_valid_thin_boundary_is_not_body_hole(self):
        self.check(mesh(),True)

    def test_explicit_thick_boundary_rejected(self):
        self.check(mesh(),False,[1])

    def test_closed_tetrahedron_both_representations(self):
        p=[(0,0,0),(1,0,0),(0,1,0),(0,0,1)]
        f=[[1,3,2],[1,2,4],[2,3,4],[3,1,4]]
        self.check(mesh(p,f,[[face] for face in f]),True,[1])

    def test_missing_all_triangles(self):
        r=self.check(mesh(triangles=[[]]),False)
        self.assertIn('triangle_count',r['face_errors'][0]['errors'])

    def test_missing_one_triangle(self):
        self.check(mesh(triangles=[[[1,2,3]]]),False)

    def test_duplicate_triangle(self):
        r=self.check(mesh(triangles=[[[1,2,3],[1,2,3]]]),False)
        self.assertIn('duplicate_triangle',r['face_errors'][0]['errors'])

    def test_reversed_triangle(self):
        self.check(mesh(triangles=[[[3,2,1],[1,3,4]]]),False)

    def test_nonmanifold_triangle_set(self):
        self.check(mesh(triangles=[[[1,2,3],[1,3,4],[1,2,4]]]),False)

    def test_triangle_not_from_parent(self):
        self.check(mesh(points=[(0,0,0),(1,0,0),(1,1,0),(0,1,0),(1,1,1)],
                        triangles=[[[1,2,3],[1,3,5]]]),False)

    def test_triangle_index_out_of_range(self):
        self.check(mesh(triangles=[[[1,2,3],[1,3,7]]]),False)

    def test_repeated_triangle_node(self):
        self.check(mesh(triangles=[[[1,2,3],[1,3,3]]]),False)

    def test_repeated_polygon_node(self):
        self.check(mesh(faces=[[1,2,3,1]]),False)

    def test_zero_polygon_area(self):
        self.check(mesh(points=[(0,0,0),(1,0,0),(2,0,0),(3,0,0)]),False)

    def test_zero_triangle_area(self):
        self.check(mesh(points=[(0,0,0),(1,0,0),(2,0,0),(0,1,0)]),False)

    def test_nonfinite(self):
        for value in (float('nan'),float('inf')):
            self.check(mesh(points=[(0,0,0),(1,0,0),(1,1,value),(0,1,0)]),False)

    def test_concave_valid_not_fan_triangulation(self):
        points=[(0,0,0),(2,0,0),(2,2,0),(1,.5,0),(0,2,0)]
        faces=[[1,2,3,4,5]]
        self.check(mesh(points,faces,[[[1,2,4],[2,3,4],[1,4,5]]]),True)

    def test_wake_nodes(self):
        self.check(mesh(wakes=[[1,2]]),True)
        self.check(mesh(wakes=[[1,9]]),False)

    def test_truncation_at_all_records(self):
        lines=mesh().splitlines()
        for i in range(len(lines)):
            self.check('\n'.join(lines[:i]),False)

    def test_unknown_version_levels_and_trailing(self):
        self.check(mesh().replace('vspgeom v3','vspgeom v2'),False)
        self.check(mesh().replace('v3\n1\n','v3\n2\n'),False)
        self.check(mesh()+'1 1 1\n',False)

    def test_missing_thick_sid(self):
        self.check(mesh(),False,[2])

    def test_uv_mapping_integrity(self):
        lines=mesh().splitlines()
        lines[-1]=lines[-1].replace('1 1 1 ','1 2 1 ',1)
        self.check('\n'.join(lines),False)
        self.check(mesh().replace('0.0','nan',1),False)

    def test_missing_file_fails_closed(self):
        r=validate_vspgeom(Path('/no-such-mesh/missing.vspgeom'))
        self.assertFalse(r['valid'])
        self.assertTrue(r['errors'])

    def test_invalid_surface_inventory_fails_closed(self):
        for ids in ([True],[1,1],[-1],['1']):
            r=validate_vspgeom(Path('missing.vspgeom'),thick_surface_ids=ids)
            self.assertFalse(r['valid'])

    def test_global_orientation_mismatch(self):
        p=[(0,0,0),(1,0,0),(0,1,0),(0,0,1)]
        f=[[1,2,3],[1,2,4],[2,3,4],[3,1,4]]
        self.check(mesh(p,f,[[face] for face in f]),False,[1])


if __name__=='__main__':
    unittest.main()
