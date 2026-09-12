"""Execute the actual chart option builder and render grouped series with ECharts SVG."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node is required to execute the browser chart renderer")
class ChartRendererTests(unittest.TestCase):
    def test_group_series_axes_legends_nulls_and_drilldown_survive_rendering(self):
        root = Path(__file__).parents[2]
        payload = {
            "spec": {"kind": "line", "layout": "overlay", "normalize": "none", "orientation": "vertical"},
            "group_by": "bank", "group_mode": "series", "title": "Synthetic grouped chart", "subtitle": "Two periods",
            "periods": ["2026-01", "2026-02"], "categories": ["Banka A", "Banka B"],
            "sources": ["Synthetic renderer fixture"],
            "series": [{"column": "value", "series_id": "group_a", "label": "Banka A", "unit": "TL", "raw_unit": "TL",
                        "values": [100, 150], "raw_values": [100, 150], "dimensions": {"bank": "A"}, "source_row_available": [True, True]},
                       {"column": "value", "series_id": "group_b", "label": "Banka B", "unit": "TL", "raw_unit": "TL",
                        "values": [20, None], "raw_values": [20, None], "dimensions": {"bank": "B"}, "source_row_available": [True, False]}],
        }
        # Expose only a test seam inside the existing closure. Its production
        # option builder and tooltip execute unchanged, including the vendored renderer.
        script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
let source = fs.readFileSync('app/static/charts.js', 'utf8');
const publicReturn = 'return { configure, clear, load, render: draw, resize, updateBusy, isSaving: () => saving };';
assert(source.includes(publicReturn));
source = source.replace(publicReturn, 'return { use: (value) => { payload = value; }, options, tooltip };');
const sandbox = { window: {}, document: {}, Intl, innerHeight: 900 };
vm.runInNewContext(source, sandbox);
const charts = sandbox.window.AnalysisCharts;
const echarts = require('./app/static/vendor/echarts.min.js');
for (const kind of ['line', 'bar', 'area']) {
  for (const orientation of kind === 'bar' ? ['vertical', 'horizontal'] : ['vertical']) {
    fixture.spec.kind = kind;
    fixture.spec.orientation = orientation;
    charts.use(fixture);
    const option = charts.options(1200, 700, true);
    const axis = orientation === 'horizontal' ? option.yAxis[0] : option.xAxis[0];
    assert.deepStrictEqual(Array.from(axis.data), fixture.periods);
    assert.deepStrictEqual(Array.from(option.legend.data), ['group_a', 'group_b']);
    assert.deepStrictEqual(Array.from(option.series.map(s => s.name)), ['group_a', 'group_b']);
    assert.strictEqual(option.series[1].data[1].value, null);
    assert.strictEqual(option.series[1].data[1].source_row_available, false);
    assert.strictEqual(option.series[1].data[0].dimensions.bank, 'B');
    assert.strictEqual(option.series[0].connectNulls, false);
    assert(option.series.every(s => !s.stack));
    const tooltip = charts.tooltip({seriesName: 'group_b', dataIndex: 0, data: option.series[1].data[0]});
    assert(tooltip.includes('Banka B') && tooltip.includes('20 TL'));
    const renderer = echarts.init(null, null, {renderer: 'svg', ssr: true, width: 1200, height: 700});
    renderer.setOption(option);
    const svg = renderer.renderToSVGString();
    assert(svg.includes('<svg') && svg.includes('Banka A') && svg.includes('Banka B'));
    renderer.dispose();
  }
}
process.stdout.write('grouped renderer passed');
"""
        result = subprocess.run(["node", "-e", script], cwd=root, input=json.dumps(payload),
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("grouped renderer passed", result.stdout)
