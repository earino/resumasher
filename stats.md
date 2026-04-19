---
layout: default
title: Community stats
description: Aggregate metrics from resumasher installs that opted into community-tier analytics.
permalink: /stats/
---

<a href="/resumasher/" style="font-size:0.85rem;color:var(--muted);text-decoration:none;">&larr; Back to resumasher</a>

# Community stats

<p>Aggregate data from resumasher installs that opted into the community
tier. Every number on this page is computed from the underlying telemetry
database via a SECURITY DEFINER Postgres function; no raw rows, no names,
no resume content, no company details leave the server. Updated live every
time you refresh.</p>

<p>If you opted in and want your data erased: run <code>resumasher
telemetry delete</code> and the right-to-erasure endpoint will wipe every
event tied to your installation ID.</p>

<div id="stats-app">
  <p id="stats-loading" class="stats-loading">Loading live data…</p>
  <div id="stats-content" hidden>
    <section class="stats-summary">
      <div class="stat-card">
        <div class="stat-num" id="stat-runs">—</div>
        <div class="stat-label">total runs</div>
      </div>
      <div class="stat-card">
        <div class="stat-num" id="stat-failures">—</div>
        <div class="stat-label">failures</div>
      </div>
      <div class="stat-card">
        <div class="stat-num" id="stat-installations">—</div>
        <div class="stat-label">installations</div>
      </div>
      <div class="stat-card">
        <div class="stat-num" id="stat-events">—</div>
        <div class="stat-label">events logged</div>
      </div>
    </section>

    <h2>Runs per day</h2>
    <div class="chart-wrap"><canvas id="chart-runs-per-day"></canvas></div>

    <h2>AI CLI host</h2>
    <p class="chart-note">Which CLI students are running resumasher in.</p>
    <div class="chart-wrap small"><canvas id="chart-hosts"></canvas></div>

    <h2>Model</h2>
    <p class="chart-note">Top 15 models self-reported by the orchestrator. Gemini in auto-routing mode shows up as different variants depending on load.</p>
    <div class="chart-wrap"><canvas id="chart-models"></canvas></div>

    <h2>Fit score distribution</h2>
    <p class="chart-note">How well the fit-analyst scored candidate-to-JD alignment (0 = bad, 10 = strong).</p>
    <div class="chart-wrap"><canvas id="chart-fit-scores"></canvas></div>

    <h2>Seniority of targeted roles</h2>
    <p class="chart-note">Bucketed via server-side <code>CASE WHEN</code> from raw LLM output. "Early-Career/Graduate" from weaker models gets normalized to <code>junior</code> here.</p>
    <div class="chart-wrap"><canvas id="chart-seniority"></canvas></div>

    <h2>Placeholder fill choices</h2>
    <p class="chart-note">When the tailor emits an <code>[INSERT…]</code> placeholder, the student picks one of three: give specifics, soften to a no-metric alternative, or drop the bullet.</p>
    <div class="chart-wrap small"><canvas id="chart-placeholders"></canvas></div>

    <h2>Failures by phase</h2>
    <p class="chart-note">Which pipeline phase most frequently hard-stops a run, grouped by error class. Phase 0 = setup, 1 = intake, 2 = mine, 3 = fit, 4 = company research, 5 = tailor, 6 = cover letter + prep, 7 = placeholder fill, 8 = PDF render, 9 = summary.</p>
    <div class="chart-wrap"><canvas id="chart-failures"></canvas></div>

    <p class="stats-generated-at">Data generated at <span id="stats-generated-at-value">—</span>.</p>
  </div>

  <div id="stats-error" hidden>
    <p>Could not load stats. <a href="#" onclick="window.location.reload(); return false;">Retry</a>.</p>
    <pre id="stats-error-detail"></pre>
  </div>
</div>

<style>
/* Stats page overrides — matches the site's warm-paper resume aesthetic. */

.stats-loading {
  color: var(--muted);
  font-style: italic;
  text-align: center;
  padding: 2rem 0;
}

.stats-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 1rem;
  margin: 2rem 0 3rem;
}
.stat-card {
  background: var(--surface);
  border-left: 3px solid var(--accent);
  padding: 1rem 1.2rem;
  border-radius: 3px;
}
.stat-num {
  font-size: 2.2rem;
  font-weight: 700;
  line-height: 1;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.stat-label {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--muted);
  margin-top: 0.4rem;
}

.chart-wrap {
  background: #fff;
  border: 1px solid #d8d5cc;
  border-radius: 4px;
  padding: 1.25rem 1rem;
  margin: 1rem 0 2.5rem;
  height: 320px;
  position: relative;
}
.chart-wrap.small {
  max-width: 520px;
  height: 260px;
}

.chart-note {
  color: var(--muted);
  font-size: 0.95rem;
  margin: 0.5rem 0 0.5rem;
}

.stats-generated-at {
  font-size: 0.85rem;
  color: var(--muted);
  text-align: center;
  margin-top: 3rem;
}
.stats-generated-at span {
  font-variant-numeric: tabular-nums;
}

#stats-error pre {
  background: var(--surface);
  padding: 1rem;
  overflow-x: auto;
  font-size: 0.85rem;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.6/dist/chart.umd.min.js"></script>
<script>
(function () {
  'use strict';

  // Public Supabase anon key. Safe to commit; RLS blocks direct table access,
  // and the only endpoint this key can call that returns aggregate data is
  // the telemetry_stats() SECURITY DEFINER function, which emits curated JSON
  // (no raw rows, no company names, no installation IDs).
  var SUPABASE_URL = 'https://ippinwwsgcycddqbnrnf.supabase.co';
  var ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImlwcGlud3dzZ2N5Y2RkcWJucm5mIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY1ODc5NjcsImV4cCI6MjA5MjE2Mzk2N30.QLuy-K2g1Cz3wqMRrJC-_Ol0WWnAuQA6JxUCFq-Y1uE';

  // Shared Chart.js styling. Matches the site's serif body + single muted accent.
  var FONT_FAMILY = 'Iowan Old Style, Palatino Linotype, Book Antiqua, Palatino, Georgia, serif';
  var COLOR_INK = '#1a1a1a';
  var COLOR_MUTED = '#6b6b6b';
  var COLOR_ACCENT = '#8b3a3a';
  var COLOR_GRID = '#e6e3da';
  // Warm earth palette that complements the single accent — used for
  // categorical charts with multiple series. Intentionally desaturated so
  // the page doesn't turn into a stock dashboard rainbow.
  var PALETTE = [
    '#8b3a3a', '#a66a3e', '#7a6b3e', '#3e6b7a', '#6b3e6b',
    '#a85a5a', '#c38a5c', '#9a8c5c', '#5c8c9a', '#8c5c8c',
    '#6b2a2a', '#8a4a1e', '#5a4b1e', '#1e4b5a', '#4b1e4b'
  ];

  Chart.defaults.font.family = FONT_FAMILY;
  Chart.defaults.color = COLOR_INK;
  Chart.defaults.borderColor = COLOR_GRID;

  function $(id) { return document.getElementById(id); }

  function paletteFor(n) {
    var out = [];
    for (var i = 0; i < n; i++) out.push(PALETTE[i % PALETTE.length]);
    return out;
  }

  // Canonical seniority order for the x-axis (lowest to highest).
  var SENIORITY_ORDER = [
    'intern', 'junior', 'mid', 'senior', 'staff',
    'manager', 'director', 'vp', 'cxo', 'unknown', 'other'
  ];

  function showError(err) {
    $('stats-loading').hidden = true;
    $('stats-content').hidden = true;
    $('stats-error').hidden = false;
    $('stats-error-detail').textContent = String(err && err.stack || err);
  }

  function render(data) {
    $('stats-loading').hidden = true;
    $('stats-content').hidden = false;

    // Summary cards
    $('stat-runs').textContent = data.summary.total_runs.toLocaleString();
    $('stat-failures').textContent = data.summary.total_failures.toLocaleString();
    $('stat-installations').textContent = data.summary.total_installations.toLocaleString();
    $('stat-events').textContent = data.summary.total_events.toLocaleString();
    $('stats-generated-at-value').textContent = data.generated_at;

    // 1. Runs per day — line chart
    new Chart($('chart-runs-per-day'), {
      type: 'line',
      data: {
        labels: data.runs_per_day.map(function (d) { return d.day; }),
        datasets: [{
          label: 'Runs',
          data: data.runs_per_day.map(function (d) { return d.runs; }),
          borderColor: COLOR_ACCENT,
          backgroundColor: COLOR_ACCENT + '20',
          fill: true,
          tension: 0.2,
          pointRadius: 3,
          pointBackgroundColor: COLOR_ACCENT
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { beginAtZero: true, ticks: { precision: 0 } },
          x: { grid: { display: false } }
        }
      }
    });

    // 2. Host distribution — pie chart
    new Chart($('chart-hosts'), {
      type: 'doughnut',
      data: {
        labels: data.host_distribution.map(function (d) { return d.host; }),
        datasets: [{
          data: data.host_distribution.map(function (d) { return d.runs; }),
          backgroundColor: paletteFor(data.host_distribution.length),
          borderColor: '#fafaf7',
          borderWidth: 2
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'right' } }
      }
    });

    // 3. Model distribution — horizontal bar
    new Chart($('chart-models'), {
      type: 'bar',
      data: {
        labels: data.model_distribution.map(function (d) { return d.model; }),
        datasets: [{
          data: data.model_distribution.map(function (d) { return d.runs; }),
          backgroundColor: COLOR_ACCENT,
          borderWidth: 0
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        indexAxis: 'y',
        plugins: { legend: { display: false } },
        scales: {
          x: { beginAtZero: true, ticks: { precision: 0 } },
          y: { grid: { display: false } }
        }
      }
    });

    // 4. Fit score distribution — bar (0-10 buckets, zero-fill gaps)
    var fitLabels = [];
    var fitCounts = [];
    for (var i = 0; i <= 10; i++) {
      fitLabels.push(String(i));
      var found = data.fit_score_distribution.find(function (d) { return d.score === i; });
      fitCounts.push(found ? found.runs : 0);
    }
    new Chart($('chart-fit-scores'), {
      type: 'bar',
      data: {
        labels: fitLabels,
        datasets: [{
          data: fitCounts,
          backgroundColor: COLOR_ACCENT,
          borderWidth: 0
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { beginAtZero: true, ticks: { precision: 0 } },
          x: {
            grid: { display: false },
            title: { display: true, text: 'Fit score (0 weak / 10 strong)', color: COLOR_MUTED }
          }
        }
      }
    });

    // 5. Seniority — bar, ordered by career progression
    var sortedSeniority = data.seniority_distribution.slice().sort(function (a, b) {
      return SENIORITY_ORDER.indexOf(a.bucket) - SENIORITY_ORDER.indexOf(b.bucket);
    });
    new Chart($('chart-seniority'), {
      type: 'bar',
      data: {
        labels: sortedSeniority.map(function (d) { return d.bucket; }),
        datasets: [{
          data: sortedSeniority.map(function (d) { return d.runs; }),
          backgroundColor: COLOR_ACCENT,
          borderWidth: 0
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { beginAtZero: true, ticks: { precision: 0 } },
          x: { grid: { display: false } }
        }
      }
    });

    // 6. Placeholder choice mix — pie
    new Chart($('chart-placeholders'), {
      type: 'doughnut',
      data: {
        labels: data.placeholder_choice_mix.map(function (d) { return d.choice; }),
        datasets: [{
          data: data.placeholder_choice_mix.map(function (d) { return d.count; }),
          backgroundColor: paletteFor(data.placeholder_choice_mix.length),
          borderColor: '#fafaf7',
          borderWidth: 2
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'right' } }
      }
    });

    // 7. Failures by phase — stacked bar by error_class, phases 0-9
    var phaseLabels = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'];
    var errorClasses = {};
    data.failure_by_phase.forEach(function (d) {
      if (!errorClasses[d.error_class]) errorClasses[d.error_class] = {};
      errorClasses[d.error_class][d.phase] = d.count;
    });
    var errorClassKeys = Object.keys(errorClasses);
    var failureDatasets = errorClassKeys.map(function (cls, i) {
      return {
        label: cls,
        data: phaseLabels.map(function (p) {
          return errorClasses[cls][parseInt(p, 10)] || 0;
        }),
        backgroundColor: PALETTE[i % PALETTE.length],
        borderWidth: 0
      };
    });
    new Chart($('chart-failures'), {
      type: 'bar',
      data: { labels: phaseLabels, datasets: failureDatasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'top' } },
        scales: {
          x: { stacked: true, grid: { display: false }, title: { display: true, text: 'Phase', color: COLOR_MUTED } },
          y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } }
        }
      }
    });
  }

  // Fetch + render on page load.
  fetch(SUPABASE_URL + '/rest/v1/rpc/telemetry_stats', {
    method: 'POST',
    headers: {
      'apikey': ANON_KEY,
      'Authorization': 'Bearer ' + ANON_KEY,
      'Content-Type': 'application/json'
    },
    body: '{}'
  })
    .then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status + ' from Supabase');
      return r.json();
    })
    .then(render)
    .catch(showError);
})();
</script>
