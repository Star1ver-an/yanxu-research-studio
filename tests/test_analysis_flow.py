"""Gating uses full events and explicit denominators; all examples synthetic."""
import io
import json
import unittest

from app import analysis_flow as flow
from app.analysis_special import inspect_special


def source(raw, name='events.csv', ident='data'):
    return dict(id=ident, name=name, raw=raw.encode() if isinstance(raw, str) else raw)


def config(gates=None, **parameters):
    return dict(method='flow_gating', inputs={'data': 'data'}, columns={'x': 'x', 'y': 'y'},
                parameters={'gates': json.dumps(gates or [dict(name='P1', type='rectangle', parent='all', bounds=[0, 2, 0, 2])]), **parameters})


def fcs_bytes(events, metadata=None):
    import flowio
    buffer = io.BytesIO()
    flowio.create_fcs(buffer, [v for event in events for v in event], ['x', 'y'], metadata_dict=metadata)
    return buffer.getvalue()


class FlowAnalysisTests(unittest.TestCase):
    def test_rectangular_parent_denominator_and_inclusive_boundaries(self):
        cfg = config([dict(name='P1', type='rectangle', bounds=[0, 2, 0, 2]),
                      dict(name='child', type='rectangle', parent='P1', bounds=[1, 2, 1, 2])])
        src = source('x,y\n0,0\n1,1\n2,2\n3,3\n-1,-1')
        result = flow.compute(cfg, [src]); gates = result['tables']['gates']['rows']
        self.assertEqual((gates[0]['count'], gates[0]['parent_count']), (3, 5))
        self.assertEqual((gates[1]['count'], gates[1]['parent_count']), (2, 3))
        self.assertAlmostEqual(gates[1]['fraction_parent'], 2 / 3)
        self.assertAlmostEqual(gates[1]['fraction_all'], 2 / 5)
        self.assertEqual(cfg['parameters'].keys(), {'gates'})
        json.dumps(result, allow_nan=False)

    def test_polygon_boundary_is_inside(self):
        cfg = config([dict(name='triangle', type='polygon', vertices=[[0, 0], [2, 0], [0, 2]])])
        result = flow.compute(cfg, [source('x,y\n0,0\n1,1\n2,0\n0,2\n2,2\n1,0.99\n1,1.01')])
        self.assertEqual(result['tables']['gates']['rows'][0]['count'], 5)

    def test_empty_parent_fraction_is_none_and_no_nan(self):
        cfg = config([dict(name='empty', type='rectangle', bounds=[10, 20, 10, 20]),
                      dict(name='child', type='rectangle', parent='empty', bounds=[0, 2, 0, 2])], display_gate='empty')
        out = flow.compute(cfg, [source('x,y\n0,0\n1,1')])
        self.assertIsNone(out['tables']['gates']['rows'][1]['fraction_parent'])
        self.assertEqual(out['statistics']['displayed_events'], 0)
        self.assertTrue(out['warnings']); json.dumps(out, allow_nan=False)

    def test_invalid_gate_cycle_self_intersection_and_code_are_rejected(self):
        cases = [([dict(name='x', parent='x', type='rectangle', bounds=[0, 1, 0, 1])], '父门'),
                 ([dict(name='x', type='rectangle', bounds=[1, 0, 0, 1])], '上界'),
                 ([dict(name='x', type='polygon', vertices=[[0, 0], [2, 2], [0, 2], [2, 0]])], '面积'),
                 ([dict(name='x', type='rectangle', bounds=[0, 1, 0, 1], python='print(1)')], '未知')]
        for gates, match in cases:
            with self.subTest(gates=gates), self.assertRaisesRegex(ValueError, match):
                flow.validate(config(gates))
        with self.assertRaisesRegex(ValueError, '明确配置'):
            flow.validate(config(gates='[]'))

    def test_compensation_recovers_known_true_events(self):
        cfg = config(compensation='file'); cfg['inputs']['compensation'] = 'matrix'
        out = flow.compute(cfg, [source('x,y\n104,30\n40,102'),
                                source('channel,x,y\nx,1,0.1\ny,0.2,1', ident='matrix')])
        points = out['tables']['display_events']['rows']
        self.assertAlmostEqual(points[0]['x'], 100)
        self.assertAlmostEqual(points[0]['y'], 20)
        self.assertAlmostEqual(points[1]['x'], 20)
        self.assertAlmostEqual(points[1]['y'], 100)

    def test_singular_matrix_and_channel_mismatch_rejected(self):
        cfg = config(compensation='file'); cfg['inputs']['compensation'] = 'matrix'
        with self.assertRaisesRegex(ValueError, '奇异'):
            flow.compute(cfg, [source('x,y\n1,2\n3,4'), source('channel,x,y\nx,1,1\ny,1,1', ident='matrix')])
        with self.assertRaisesRegex(ValueError, '通道未'):
            flow.compute(cfg, [source('x,y\n1,2\n3,4'), source('channel,a,b\na,1,0\nb,0,1', ident='matrix')])

    def test_log_does_not_silently_drop_negative_events(self):
        with self.assertRaisesRegex(ValueError, '零/负'):
            flow.compute(config(transform='log10'), [source('x,y\n0,1\n-1,2\n3,4')])
        out = flow.compute(config(transform='arcsinh', cofactor=1), [source('x,y\n0,1\n-1,2\n3,4')])
        self.assertEqual(out['statistics']['events'], 3)
        self.assertLess(out['tables']['display_events']['rows'][1]['x'], 0)

    def test_downsampling_never_changes_gating_denominators_or_histogram(self):
        text = 'x,y\n' + '\n'.join(f'{i % 5},{i % 5}' for i in range(1000))
        cfg = config(display_events=100, seed=9)
        a = flow.compute(cfg, [source(text)]); b = flow.compute(cfg, [source(text)])
        self.assertEqual(a['tables']['display_events'], b['tables']['display_events'])
        self.assertEqual(a['tables']['gates']['rows'][0]['count'], 600)
        self.assertEqual(a['statistics']['displayed_events'], 100)
        self.assertEqual(sum(r['count'] for r in a['tables']['histogram']['rows']), 1000)
        self.assertEqual(sum(r['count'] for r in a['tables']['density']['rows']), 1000)

    def test_fcs_round_trip_matches_csv_linear_events(self):
        raw = fcs_bytes([[0, 0], [1, 1], [2, 2], [3, 3]])
        info = inspect_special(raw, 'synthetic.fcs')
        self.assertEqual(info['row_count'], 4)
        self.assertEqual([c['name'] for c in info['columns']], ['x', 'y'])
        out = flow.compute(config(), [source(raw, 'synthetic.fcs')])
        self.assertEqual(out['tables']['gates']['rows'][0]['count'], 3)
        self.assertEqual(out['tables']['display_events']['rows'][1]['x'], 1)

    def test_fcs_existing_spill_applies_only_when_requested(self):
        raw = fcs_bytes([[104, 30], [40, 102]], {'spill': '2,x,y,1,0.1,0.2,1'})
        self.assertTrue(inspect_special(raw, 'test.fcs')['has_compensation'])
        out = flow.compute(config(compensation='fcs'), [source(raw, 'test.fcs')])
        self.assertAlmostEqual(out['tables']['display_events']['rows'][0]['x'], 100, places=5)
        original = flow.compute(config(), [source(raw, 'test.fcs')])
        self.assertEqual(original['tables']['display_events']['rows'][0]['x'], 104)

    def test_fcs_header_and_enormous_declared_count_fail_before_loading(self):
        with self.assertRaisesRegex(ValueError, '文件头'):
            inspect_special(b'badfcs' * 20, 'bad.fcs')
        raw = fcs_bytes([[1, 2], [3, 4]])
        start, end = int(raw[10:18]), int(raw[18:26])
        segment = raw[start:end + 1]
        delimiter = chr(segment[0])
        text = segment.decode('ascii')
        # Preserve text offsets by changing the declared channel number to zero.
        text = text.replace(delimiter + '$PAR' + delimiter + '2' + delimiter,
                            delimiter + '$PAR' + delimiter + '0' + delimiter)
        corrupted = raw[:start] + text.encode() + raw[end + 1:]
        with self.assertRaisesRegex(ValueError, '最多'):
            inspect_special(corrupted, 'bad.fcs')

    def test_draw_views_are_actual_pngs(self):
        for view in ['scatter', 'density', 'histogram']:
            cfg = flow.validate(config(view=view))
            out = flow.compute(cfg, [source('x,y\n0,0\n1,1\n2,2\n3,3')])
            fig = flow.draw(out, cfg, {'width': 5, 'height': 4, 'dpi': 80})
            stream = io.BytesIO(); fig.savefig(stream, format='png')
            self.assertTrue(stream.getvalue().startswith(b'\x89PNG'))


if __name__ == '__main__':
    unittest.main()
