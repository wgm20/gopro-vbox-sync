from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import math
import unittest
import xml.etree.ElementTree as ET

from goprovbox.engine import Match
from goprovbox.fullscene import parse_full_scene, scene_source
from goprovbox.gpmf import TelemetryError
from goprovbox.overlay import Renderer
from goprovbox.scene_math import SceneMath
from tests.export_case import ExportTestCase
from tests.test_core import BASE, video
from tests.test_overlay import four_channel_vbo, scene_members


GEAR_EXPRESSION = (
    '(std_Speed>=16.09344)*(1+(add_RPM<=107.12439354171637*std_Speed)'
    '+(add_RPM<=69.84212200747633*std_Speed)+(add_RPM<=50.95243776346138*std_Speed)'
    '+(add_RPM<=39.8298934224131*std_Speed)+(add_RPM<=31.068559611866696*std_Speed))'
)
GEAR_SOURCE = ('maths.eqn{' + GEAR_EXPRESSION
               + ';ds:std_Speed{vbox.speed_gnd_mps scaling=3.6}'
               + ';ds:add_RPM{can.bin[2].channel(RPM)}}')


def formula(expression, declarations='ds:x{vbox.speed_gnd_mps}'):
    return 'maths.eqn{' + expression + ';' + declarations + '}'


def gear_members():
    """A gear-only scene proves that its hidden input dependencies are checked."""
    members = scene_members()
    root = ET.fromstring(members['SCENE.XML'])
    graphics, layout = root.find('.//graphics'), root.find('.//layout')
    for element in list(graphics):
        if element.get('name') != 'speed':
            graphics.remove(element)
    for element in list(layout):
        if element.get('name') != 'speed':
            layout.remove(element)
    text, placement = graphics[0], layout[0]
    text.set('name', 'Calculated_Gear_Display')
    text.set('datasource', GEAR_SOURCE)
    text.set('fmtstr', '%01.0lf')
    text.set('fmtfn', 'float64')
    placement.set('name', 'Calculated_Gear_Display')
    members['SCENE.XML'] = ET.tostring(root)
    return members


class SceneMathTests(unittest.TestCase):
    def test_actual_native_gear_formula_uses_scaled_inputs_and_calibrated_gears(self):
        parsed, scale = scene_source(GEAR_SOURCE)
        self.assertIsInstance(parsed, SceneMath)
        self.assertEqual(scale, 1)
        self.assertEqual(dict(parsed.bindings)['std_Speed'], ('vbox.speed_gnd_mps', 3.6))
        for gear, mph, rpm in ((1, 20, 4500), (2, 40, 5542.84), (3, 60, 5652),
                               (4, 70, 5074.895), (5, 80, 4598.216), (6, 100, 4420.77),
                               (0, 9.99, 5000), (0, 0, 0)):
            values = {'vbox.speed_gnd_mps': mph * 1.609344 / 3.6,
                      'can.bin[2].channel(RPM)': rpm}
            with self.subTest(gear=gear, mph=mph):
                self.assertEqual(parsed.evaluate(lambda source: values[source[0]] * source[1]), gear)

    def test_single_comparisons_are_numeric_and_boundaries_are_inclusive(self):
        for expression, expected in (('(x>=10)*(1+(x<=10))', 2), ('x<10', 0),
                                     ('x!=10', 0), ('x==10', 1), ('x>9', 1)):
            with self.subTest(expression=expression):
                parsed, _ = scene_source(formula(expression))
                self.assertEqual(parsed.evaluate(lambda _: 10), expected)

    def test_arithmetic_and_reordered_named_bindings(self):
        parsed, _ = scene_source(formula('(-x+2*y)/+2',
                                         'ds:y{can.bin[2].channel(RPM)};ds:x{vbox.speed_gnd_mps scaling=3.6}'))
        self.assertEqual(parsed.evaluate(lambda source: 5 if source[0].startswith('can.') else 4), 3)

    def test_missing_nonfinite_and_invalid_numeric_results_are_unavailable(self):
        gear, _ = scene_source(GEAR_SOURCE)
        for missing in (None, math.nan, math.inf, -math.inf):
            with self.subTest(missing=missing):
                self.assertIsNone(gear.evaluate(lambda source: missing if source[0].startswith('can.') else 0))
        for expression in ('1/(x-x)', 'x*1e308'):
            parsed, _ = scene_source(formula(expression))
            self.assertIsNone(parsed.evaluate(lambda _: 10))

    def test_malformed_duplicate_unbound_and_unused_bindings_are_rejected(self):
        for source in (formula('x', 'ds:x{vbox.speed_gnd_mps};ds:x{vbox.sats_used}'),
                       formula('y'), formula('x', 'ds:x{vbox.speed_gnd_mps};ds:y{vbox.sats_used}'),
                       formula('x', 'ds:x{}'), formula('x', 'ds:x{vbox.speed_gnd_mps scaling=1 scaling=2}'),
                       formula('x', 'ds:x{maths.eqn{x;ds:x{vbox.speed_gnd_mps}}}'),
                       GEAR_SOURCE + 'trailing', GEAR_SOURCE[:-1], 'maths.eqn{x}'):
            with self.subTest(source=source), self.assertRaises(TelemetryError):
                scene_source(source)

    def test_scene_text_cannot_invoke_python_or_unsupported_operators(self):
        expressions = ("__import__('os').system('echo unsafe')", 'x.__class__', 'x[0]',
                       '(lambda: x)()', '[x for x in (1,2)]', '(x:=1)', 'True*x',
                       'x**1000000', 'x and x', 'not x', '0<x<100', 'Sqrt(x)', '1e999*x')
        for expression in expressions:
            with self.subTest(expression=expression), self.assertRaises(TelemetryError):
                scene_source(formula(expression))

    def test_source_expression_ast_depth_node_and_binding_limits(self):
        balanced = 'x'
        for _ in range(7):
            balanced = '(' + balanced + '+' + balanced + ')'
        for source in (formula('x+' + '1' * 4100), formula('-' * 40 + 'x'),
                       formula(balanced),
                       formula('x', ';'.join(f'ds:v{i}{{vbox.speed_gnd_mps}}' for i in range(17))),
                       formula('x', 'ds:x{' + 'v' * 257 + '}'),
                       'maths.eqn{' + 'x' * 10_000 + '}'):
            with self.subTest(source=source[:60]), self.assertRaises(TelemetryError):
                scene_source(source)


class SceneMathIntegrationTests(ExportTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.vbo = four_channel_vbo(self.folder)
        self.clip = replace(video(self.folder / 'video.mp4', duration=3), width=320, height=180)
        self.match = Match(self.vbo, self.clip, self.vbo.rows, 0, 0)
        self.scene = parse_full_scene(gear_members())

    def renderer(self, match=None):
        return Renderer(self.clip, [match or self.match], 320, 180, self.scene)

    def set_inputs(self, rpm, speed_kmh):
        for row in self.vbo.rows:
            row.values[self.vbo.index('rpm')] = str(rpm)
            row.values[self.vbo.index('velocity')] = str(speed_kmh)

    def test_actual_native_expression_parses_and_renders_discrete_gear(self):
        self.assertEqual(self.scene.bindings, {})
        for gear, mph, rpm in ((2, 40, 5542.84), (3, 60, 5652), (4, 70, 5074.895),
                               (5, 80, 4598.216), (6, 100, 4420.77), (0, 9, 4500)):
            self.set_inputs(rpm, mph * 1.609344)
            renderer = self.renderer()
            self.assertIsNotNone(renderer.frame(1.05).getbbox())
            with self.subTest(gear=gear):
                self.assertEqual(renderer.text_cache['Calculated_Gear_Display'][0], str(gear))

    def test_formula_is_evaluated_after_interpolation_not_interpolated_as_gear(self):
        self.set_inputs(5000, 100)
        a, b = self.vbo.rows[10:12]
        a.values[self.vbo.index('rpm')] = '5000'  # 4th
        b.values[self.vbo.index('rpm')] = '5200'  # 3rd
        renderer = self.renderer()
        renderer.frame(1.05)
        self.assertEqual(renderer.text_cache['Calculated_Gear_Display'][0], '3')

    def test_hidden_rpm_dependency_requires_recorded_column_and_matching_unit(self):
        index = self.vbo.index('rpm')
        columns = [c for i, c in enumerate(self.vbo.columns) if i != index]
        rows = [replace(row, values=[v for i, v in enumerate(row.values) if i != index]) for row in self.vbo.rows]
        missing = replace(self.vbo, columns=columns, rows=rows)
        with self.assertRaisesRegex(TelemetryError, 'RPM is missing'):
            self.renderer(replace(self.match, vbo=missing, rows=rows))
        marker = self.vbo.preamble.index('[channel units]')
        unit = next(i for i, line in enumerate(self.vbo.preamble) if i > marker and line.strip().lower() == 'rpm')
        self.vbo.preamble[unit] = 'krpm'
        with self.assertRaisesRegex(TelemetryError, 'RPM unit is krpm, scene expects rpm'):
            self.renderer()

    def test_velocity_dependency_requires_kmh_data_and_has_no_zero_fallback(self):
        header = self.vbo.preamble.index('velocity kmh')
        self.vbo.preamble[header] = 'velocity mph'
        with self.assertRaisesRegex(TelemetryError, 'velocity kmh'):
            self.renderer()
        self.vbo.preamble[header] = 'velocity kmh'
        renderer = self.renderer()
        session = next(iter(renderer.sessions.values()))
        source = self.scene.elements[0]['source']
        self.assertIsNone(renderer.source_value(source, session, BASE, {'rpm': 5000}, {}))
        self.assertIsNone(renderer.source_value(source, session, BASE, {'rpm': 5000, 'velocity': math.inf}, {}))

    def test_math_overlay_stays_hidden_outside_coverage_and_in_gaps(self):
        match = replace(self.match, rows=[self.vbo.rows[0], self.vbo.rows[10]])
        renderer = self.renderer(match)
        self.assertIsNone(renderer.frame(.5).getbbox())
        self.assertIsNone(renderer.frame(2).getbbox())

    def test_unsupported_bound_source_fails_before_any_frame_is_encoded(self):
        members = gear_members()
        members['SCENE.XML'] = members['SCENE.XML'].replace(b'vbox.speed_gnd_mps scaling=3.6', b'unsupported.source')
        self.scene = parse_full_scene(members)
        with self.assertRaisesRegex(TelemetryError, 'Unsupported scene data source.*unsupported.source'):
            self.renderer()


if __name__ == '__main__':
    unittest.main()
