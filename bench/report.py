"""
report.py — generate a self-contained HTML report from bench_results.json

Usage:
    python report.py                        # reads bench_results.json
    python report.py --input my_run.json    # custom input
    python report.py --open                 # open browser after generating
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Relay vs LiteLLM — Benchmark Report</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0d0f14;
    --surface: #161a23;
    --border: #252a38;
    --relay: #00e5ff;
    --litellm: #ff6b6b;
    --text: #e2e8f0;
    --muted: #64748b;
    --green: #22c55e;
    --yellow: #f59e0b;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    padding: 2rem;
    min-height: 100vh;
  }
  h1 {
    font-size: 1.6rem;
    font-weight: 700;
    letter-spacing: -0.03em;
    margin-bottom: 0.25rem;
  }
  .subtitle { color: var(--muted); font-size: 0.8rem; margin-bottom: 2.5rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 1.25rem; }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1.25rem 1.5rem;
  }
  .card h2 { font-size: 0.75rem; color: var(--muted); text-transform: uppercase;
             letter-spacing: 0.1em; margin-bottom: 1rem; }
  .stat-row { display: flex; justify-content: space-between; align-items: center;
              padding: 0.4rem 0; border-bottom: 1px solid var(--border); font-size: 0.85rem; }
  .stat-row:last-child { border-bottom: none; }
  .stat-label { color: var(--muted); }
  .winner { color: var(--green); font-weight: 600; }
  .loser  { color: var(--text); }
  .badge {
    font-size: 0.7rem; padding: 2px 8px; border-radius: 4px;
    font-weight: 700; letter-spacing: 0.05em;
  }
  .badge-relay   { background: rgba(0,229,255,0.12); color: var(--relay); }
  .badge-litellm { background: rgba(255,107,107,0.12); color: var(--litellm); }
  .chart-wrap { position: relative; height: 220px; }
  .full-width { grid-column: 1 / -1; }
  .section-title {
    font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.15em;
    color: var(--muted); margin: 2rem 0 1rem;
  }
  table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
  th { color: var(--muted); text-align: left; padding: 0.5rem 0.75rem;
       border-bottom: 1px solid var(--border); font-weight: 500; }
  td { padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border); }
  tr:last-child td { border-bottom: none; }
  .highlight { color: var(--green); font-weight: 600; }
  .summary-bar {
    display: flex; gap: 2rem; padding: 1rem 1.5rem;
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    margin-bottom: 2rem; flex-wrap: wrap;
  }
  .summary-item { display: flex; flex-direction: column; gap: 0.2rem; }
  .summary-val  { font-size: 1.4rem; font-weight: 700; }
  .summary-lbl  { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }
  .relay-color   { color: var(--relay); }
  .litellm-color { color: var(--litellm); }
</style>
</head>
<body>

<h1>⚡ Relay vs LiteLLM — Benchmark Report</h1>
<p class="subtitle" id="ts"></p>

<div class="summary-bar" id="summary-bar"></div>

<div id="payload-sections"></div>

<script>
const DATA = __DATA__;

document.getElementById('ts').textContent =
  'Generated ' + new Date(DATA.timestamp).toLocaleString() +
  ' · ' + DATA.results[0]?.total_requests + ' requests · concurrency ' + DATA.results[0]?.concurrency;

const COLORS = { relay: '#00e5ff', litellm: '#ff6b6b' };
const RELAY_NAMES   = ['relay'];
const LITELLM_NAMES = ['litellm'];

function color(name) {
  return RELAY_NAMES.includes(name.toLowerCase()) ? COLORS.relay : COLORS.litellm;
}
function badgeClass(name) {
  return RELAY_NAMES.includes(name.toLowerCase()) ? 'badge-relay' : 'badge-litellm';
}

// Group results by payload type
const byPayload = {};
DATA.results.forEach(r => {
  if (!byPayload[r.payload]) byPayload[r.payload] = [];
  byPayload[r.payload].push(r);
});

// Summary bar
const allRelay   = DATA.results.filter(r => RELAY_NAMES.includes(r.name));
const allLitellm = DATA.results.filter(r => !RELAY_NAMES.includes(r.name));
const bar = document.getElementById('summary-bar');
if (allRelay.length && allLitellm.length) {
  const avgRpsRelay   = allRelay.reduce((s,r)=>s+r.rps,0)/allRelay.length;
  const avgRpsLit     = allLitellm.reduce((s,r)=>s+r.rps,0)/allLitellm.length;
  const avgP50Relay   = allRelay.reduce((s,r)=>s+r.latency_ms.p50,0)/allRelay.length;
  const avgP50Lit     = allLitellm.reduce((s,r)=>s+r.latency_ms.p50,0)/allLitellm.length;
  const rpsWinner     = avgRpsRelay > avgRpsLit ? 'relay' : 'litellm';
  const latWinner     = avgP50Relay < avgP50Lit ? 'relay' : 'litellm';
  bar.innerHTML = `
    <div class="summary-item">
      <span class="summary-val relay-color">${avgRpsRelay.toFixed(1)}</span>
      <span class="summary-lbl">relay avg req/s</span>
    </div>
    <div class="summary-item">
      <span class="summary-val litellm-color">${avgRpsLit.toFixed(1)}</span>
      <span class="summary-lbl">litellm avg req/s</span>
    </div>
    <div class="summary-item">
      <span class="summary-val relay-color">${avgP50Relay.toFixed(0)}ms</span>
      <span class="summary-lbl">relay median latency</span>
    </div>
    <div class="summary-item">
      <span class="summary-val litellm-color">${avgP50Lit.toFixed(0)}ms</span>
      <span class="summary-lbl">litellm median latency</span>
    </div>
    <div class="summary-item">
      <span class="summary-val" style="color:var(--green)">${rpsWinner} faster</span>
      <span class="summary-lbl">throughput winner</span>
    </div>
    <div class="summary-item">
      <span class="summary-val" style="color:var(--green)">${latWinner} lower latency</span>
      <span class="summary-lbl">latency winner</span>
    </div>
  `;
}

const container = document.getElementById('payload-sections');

Object.entries(byPayload).forEach(([payload, results]) => {
  const section = document.createElement('div');
  section.innerHTML = `<p class="section-title">Payload: ${payload}</p>`;

  const grid = document.createElement('div');
  grid.className = 'grid';

  // ── Latency breakdown bar chart ──────────────────────────────────────────
  const latCard = document.createElement('div');
  latCard.className = 'card';
  latCard.innerHTML = `<h2>Latency Breakdown (ms)</h2><div class="chart-wrap"><canvas id="lat-${payload}"></canvas></div>`;
  grid.appendChild(latCard);

  // ── RPS chart ────────────────────────────────────────────────────────────
  const rpsCard = document.createElement('div');
  rpsCard.className = 'card';
  rpsCard.innerHTML = `<h2>Throughput (req/s)</h2><div class="chart-wrap"><canvas id="rps-${payload}"></canvas></div>`;
  grid.appendChild(rpsCard);

  // ── Latency distribution (p50/p90/p99) ──────────────────────────────────
  const distCard = document.createElement('div');
  distCard.className = 'card';
  distCard.innerHTML = `<h2>Latency Percentiles (ms)</h2><div class="chart-wrap"><canvas id="pct-${payload}"></canvas></div>`;
  grid.appendChild(distCard);

  // ── Detailed stats table ─────────────────────────────────────────────────
  const tableCard = document.createElement('div');
  tableCard.className = 'card';
  const rows = results.map(r => {
    const lat = r.latency_ms;
    return `<tr>
      <td><span class="badge ${badgeClass(r.name)}">${r.name}</span></td>
      <td>${r.rps.toFixed(1)}</td>
      <td>${lat.mean.toFixed(0)}</td>
      <td>${lat.p50.toFixed(0)}</td>
      <td>${lat.p90.toFixed(0)}</td>
      <td>${lat.p99.toFixed(0)}</td>
      <td>${r.success_rate_pct.toFixed(1)}%</td>
      <td>${r.tokens_per_sec.toFixed(0)}</td>
    </tr>`;
  }).join('');
  tableCard.innerHTML = `
    <h2>Full Stats</h2>
    <table>
      <thead><tr>
        <th>Proxy</th><th>req/s</th><th>mean</th>
        <th>p50</th><th>p90</th><th>p99</th>
        <th>success</th><th>tok/s</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  grid.appendChild(tableCard);

  section.appendChild(grid);
  container.appendChild(section);

  // ── Render charts after DOM is ready ─────────────────────────────────────
  requestAnimationFrame(() => {
    const labels     = results.map(r => r.name);
    const bgColors   = results.map(r => color(r.name) + '33');
    const brdColors  = results.map(r => color(r.name));

    // Latency breakdown (grouped bar: min/mean/p90/p99)
    new Chart(document.getElementById(`lat-${payload}`), {
      type: 'bar',
      data: {
        labels: ['min', 'mean', 'p50', 'p90', 'p99', 'max'],
        datasets: results.map(r => ({
          label: r.name,
          data: [r.latency_ms.min, r.latency_ms.mean, r.latency_ms.p50,
                 r.latency_ms.p90, r.latency_ms.p99, r.latency_ms.max],
          backgroundColor: color(r.name) + '44',
          borderColor: color(r.name),
          borderWidth: 1.5,
          borderRadius: 3,
        }))
      },
      options: _chartOpts('ms', true)
    });

    // RPS horizontal bar
    new Chart(document.getElementById(`rps-${payload}`), {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'req/s',
          data: results.map(r => r.rps),
          backgroundColor: bgColors,
          borderColor: brdColors,
          borderWidth: 1.5,
          borderRadius: 4,
        }]
      },
      options: _chartOpts('req/s', false, true)
    });

    // Percentile radar / line
    new Chart(document.getElementById(`pct-${payload}`), {
      type: 'line',
      data: {
        labels: ['p50', 'p90', 'p99', 'max'],
        datasets: results.map(r => ({
          label: r.name,
          data: [r.latency_ms.p50, r.latency_ms.p90, r.latency_ms.p99, r.latency_ms.max],
          borderColor: color(r.name),
          backgroundColor: color(r.name) + '18',
          borderWidth: 2,
          pointRadius: 4,
          fill: true,
          tension: 0.3,
        }))
      },
      options: _chartOpts('ms', true)
    });
  });
});

function _chartOpts(unit, darkGrid = false, horizontal = false) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    indexAxis: horizontal ? 'y' : 'x',
    plugins: {
      legend: { labels: { color: '#94a3b8', font: { family: 'monospace', size: 11 } } }
    },
    scales: {
      x: { ticks: { color: '#64748b', font: { size: 10 } },
           grid: { color: '#1e2433' } },
      y: { ticks: { color: '#64748b', font: { size: 10 } },
           grid: { color: '#1e2433' } },
    }
  };
}
</script>
</body>
</html>
"""


def generate(input_path: str = "bench_results.json", output_path: str = "bench_report.html"):
    with open(input_path) as f:
        data = json.load(f)

    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(data))
    with open(output_path, "w") as f:
        f.write(html)
    print(f"Report generated → {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Generate HTML benchmark report")
    parser.add_argument("--input",  default="bench_results.json")
    parser.add_argument("--output", default="bench_report.html")
    parser.add_argument("--open",   action="store_true", help="Open in browser")
    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    out = generate(args.input, args.output)
    if args.open:
        import webbrowser
        webbrowser.open(f"file://{os.path.abspath(out)}")


if __name__ == "__main__":
    main()
