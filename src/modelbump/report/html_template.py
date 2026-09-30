"""Single-file HTML report template (F-REP-1).

Inline CSS/JS, no network, dark and light themes, filter by flag and tag,
side-by-side outputs, per-sample toggles, and a copy button for the reproduce
command. The report data is injected as JSON (``__MODELBUMP_DATA__``) and the
page title as ``__MODELBUMP_TITLE__``.
"""

from __future__ import annotations

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__MODELBUMP_TITLE__</title>
<style>
  :root{
    --bg:#0b1020; --bg-soft:#121a33; --card:#161f3d; --card-2:#1c2750;
    --fg:#e9eefc; --muted:#8fa0c8; --line:#26325c;
    --accent:#7c5cff; --accent-2:#22d3ee;
    --pass:#22c55e; --fail:#ef4444; --warn:#f59e0b;
    --flag:#f472b6; --chip:#22305c;
    --shadow:0 12px 32px rgba(0,0,0,.45);
  }
  html[data-theme="light"]{
    --bg:#f6f7fc; --bg-soft:#eceffa; --card:#ffffff; --card-2:#f2f4ff;
    --fg:#141a2e; --muted:#5a6785; --line:#dfe3f2;
    --shadow:0 10px 28px rgba(30,40,90,.12);
  }
  *{box-sizing:border-box}
  body{
    margin:0; background:
      radial-gradient(1200px 500px at 10% -10%, rgba(124,92,255,.22), transparent 60%),
      radial-gradient(900px 420px at 100% 0%, rgba(34,211,238,.16), transparent 55%),
      var(--bg);
    color:var(--fg); font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Inter,sans-serif;
    min-height:100vh;
  }
  a{color:var(--accent-2)}
  header{padding:34px 26px 18px;max-width:1360px;margin:0 auto}
  .brand{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .logo{
    width:38px;height:38px;border-radius:11px;
    background:linear-gradient(135deg,var(--accent),var(--accent-2));
    display:grid;place-items:center;font-weight:800;font-size:19px;color:#08101f;
    box-shadow:var(--shadow);
  }
  h1{margin:0;font-size:23px;letter-spacing:-.02em}
  .route{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;color:var(--muted)}
  .verdict{
    margin-left:auto;padding:9px 18px;border-radius:999px;font-weight:800;letter-spacing:.06em;
    font-size:13px;box-shadow:var(--shadow);
  }
  .verdict.pass{background:linear-gradient(135deg,#16a34a,#22c55e);color:#04140a}
  .verdict.fail{background:linear-gradient(135deg,#dc2626,#f87171);color:#1c0606}
  main{max-width:1360px;margin:0 auto;padding:0 26px 60px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:14px;margin:18px 0 10px}
  .card{
    background:linear-gradient(160deg,var(--card),var(--card-2));
    border:1px solid var(--line); border-radius:16px; padding:15px 17px; box-shadow:var(--shadow);
  }
  .card .k{font-size:11px;text-transform:uppercase;letter-spacing:.10em;color:var(--muted)}
  .card .v{font-size:25px;font-weight:800;margin-top:5px;letter-spacing:-.02em}
  .card .sub{font-size:12px;color:var(--muted);margin-top:3px}
  .up{color:var(--fail)} .down{color:var(--pass)} .flat{color:var(--muted)}
  h2{font-size:16px;margin:30px 0 12px;letter-spacing:-.01em}
  .violations{background:rgba(239,68,68,.10);border:1px solid rgba(239,68,68,.4);border-radius:14px;padding:14px 18px}
  .violations li{margin:5px 0}
  .warnings{background:rgba(245,158,11,.10);border:1px solid rgba(245,158,11,.4);border-radius:14px;padding:14px 18px}
  code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  .toolbar{
    display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:14px 0;
    background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px;
  }
  .toolbar input,.toolbar select{
    background:var(--bg-soft);border:1px solid var(--line);color:var(--fg);
    border-radius:9px;padding:8px 11px;font-size:13px;
  }
  .chip{
    display:inline-flex;align-items:center;gap:6px;padding:5px 11px;border-radius:999px;
    background:var(--chip);border:1px solid var(--line);font-size:12px;cursor:pointer;user-select:none;
  }
  .chip.on{background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#0a0f1f;font-weight:700;border-color:transparent}
  .chip .n{opacity:.7}
  table{width:100%;border-collapse:collapse;font-size:13.5px}
  th,td{padding:9px 11px;border-bottom:1px solid var(--line);text-align:right}
  th:first-child,td:first-child{text-align:left}
  thead th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.06em}
  tbody tr:hover{background:var(--bg-soft)}
  .case{
    background:var(--card);border:1px solid var(--line);border-radius:16px;
    margin:14px 0;overflow:hidden;box-shadow:var(--shadow);
  }
  .case > summary{
    padding:15px 18px;cursor:pointer;display:flex;gap:12px;align-items:center;flex-wrap:wrap;
    list-style:none;
  }
  .case > summary::-webkit-details-marker{display:none}
  .case[open] > summary{border-bottom:1px solid var(--line);background:var(--bg-soft)}
  .case-id{font-weight:700;font-family:ui-monospace,monospace}
  .drift{
    font-weight:800;font-size:12px;padding:3px 10px;border-radius:999px;
    background:linear-gradient(135deg,var(--flag),var(--accent));color:#160d24;
  }
  .flag{
    font-size:11.5px;padding:3px 9px;border-radius:999px;border:1px solid var(--line);
    background:var(--chip);font-family:ui-monospace,monospace;
  }
  .tag{font-size:11.5px;padding:3px 9px;border-radius:999px;background:rgba(124,92,255,.18);
    border:1px solid rgba(124,92,255,.4);color:var(--fg)}
  .case-body{padding:16px 18px}
  .detail{color:var(--muted);font-size:12.5px;margin:2px 0}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px}
  @media (max-width:900px){.grid2{grid-template-columns:1fr}}
  .out{
    background:var(--bg-soft);border:1px solid var(--line);border-radius:12px;padding:12px;
    white-space:pre-wrap;word-break:break-word;font-size:12.5px;max-height:320px;overflow:auto;
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  }
  .out-head{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px}
  .samples{display:flex;gap:6px;flex-wrap:wrap}
  .samples button{
    background:var(--bg-soft);border:1px solid var(--line);color:var(--fg);border-radius:7px;
    padding:3px 9px;font-size:11.5px;cursor:pointer;
  }
  .samples button.on{background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#0a0f1f;font-weight:700;border-color:transparent}
  .metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:12px}
  .metric{background:var(--bg-soft);border:1px solid var(--line);border-radius:10px;padding:9px 11px}
  .metric .mk{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em}
  .metric .mv{font-size:14px;font-weight:700;margin-top:2px}
  .repro{
    background:var(--bg-soft);border:1px solid var(--line);border-radius:12px;padding:13px 15px;
    display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;
  }
  .repro pre{margin:0;white-space:pre-wrap;word-break:break-all;font-size:12.5px}
  .btn{
    background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#0a0f1f;border:0;
    border-radius:9px;padding:8px 14px;font-weight:700;font-size:12.5px;cursor:pointer;
  }
  footer{max-width:1360px;margin:0 auto;padding:20px 26px 46px;color:var(--muted);font-size:12.5px}
  .theme-toggle{margin-left:10px;background:var(--card);border:1px solid var(--line);color:var(--fg);
    border-radius:9px;padding:8px 12px;cursor:pointer;font-size:13px}
  .hidden{display:none !important}
  .empty{color:var(--muted);padding:26px;text-align:center}
  .lang-badge{font-size:11px;padding:2px 7px;border-radius:6px;background:var(--chip);border:1px solid var(--line)}
</style>
</head>
<body>
<header>
  <div class="brand">
    <div class="logo">Δ</div>
    <div>
      <h1>modelbump behavioral diff</h1>
      <div class="route" id="route">—</div>
    </div>
    <div class="verdict" id="verdict">—</div>
    <button class="theme-toggle" id="theme">◐ theme</button>
  </div>
</header>

<main>
  <div class="cards" id="cards"></div>
  <div id="alerts"></div>

  <h2>Suite comparison</h2>
  <div style="overflow:auto"><table id="suite-table"><thead><tr>
    <th>Metric</th><th>A</th><th>B</th><th>Δ</th>
  </tr></thead><tbody></tbody></table></div>

  <h2>Per-tag breakdown</h2>
  <div style="overflow:auto"><table id="tag-table"><thead><tr>
    <th>Tag</th><th>Cases</th><th>Flagged</th><th>Drift rate</th><th>Top flag</th>
  </tr></thead><tbody></tbody></table></div>

  <h2>Cases</h2>
  <div class="toolbar">
    <input id="search" type="search" placeholder="search id, tag, or text…" style="min-width:230px">
    <span id="flag-filters" style="display:flex;gap:7px;flex-wrap:wrap"></span>
    <span id="tag-filters" style="display:flex;gap:7px;flex-wrap:wrap"></span>
    <label class="chip" style="margin-left:auto">
      <input type="checkbox" id="only-drifted" checked style="accent-color:var(--accent)"> only flagged
    </label>
  </div>
  <div id="cases-flags"></div>
  <div id="cases"></div>
  <div id="cases-empty" class="empty hidden">No cases match the current filters.</div>

  <h2>Reproduce</h2>
  <div class="repro">
    <pre id="repro-command"></pre>
    <button class="btn" id="copy">copy command</button>
  </div>
  <div class="cards" id="meta-cards" style="margin-top:14px"></div>
</main>

<footer>
  Generated by
  <a href="https://github.com/iggym/modelbump">modelbump</a>
  <span id="foot-version"></span> · <span id="foot-time"></span>
</footer>

<script id="modelbump-data" type="application/json">__MODELBUMP_DATA__</script>
<script>
(function(){
  const D = JSON.parse(document.getElementById('modelbump-data').textContent);
  const S = D.summary || {};
  const A = S.a || {}, B = S.b || {};
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

  /* -- theme ---------------------------------------------------------- */
  const root = document.documentElement;
  try { const saved = localStorage.getItem('modelbump-theme'); if (saved) root.dataset.theme = saved; } catch(e){}
  document.getElementById('theme').onclick = () => {
    root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem('modelbump-theme', root.dataset.theme); } catch(e){}
  };

  /* -- header --------------------------------------------------------- */
  document.getElementById('route').textContent =
    (D.from && D.from.spec || '?') + '   →   ' + (D.to && D.to.spec || '?');
  const pass = !!(D.verdict && D.verdict.pass);
  const v = document.getElementById('verdict');
  v.textContent = pass ? 'PASS' : 'FAIL';
  v.className = 'verdict ' + (pass ? 'pass' : 'fail');
  document.title = (pass ? '✅ ' : '❌ ') + (D.from && D.from.spec) + ' → ' + (D.to && D.to.spec);

  /* -- cards ---------------------------------------------------------- */
  const pct = (x) => x == null ? '—' : (x*100).toFixed(1) + '%';
  const ms  = (x) => x == null ? '—' : Math.round(x).toLocaleString() + ' ms';
  const usd = (x) => x == null ? '—' : '$' + Number(x).toFixed(4);
  const num = (x, d=1) => x == null ? '—' : Number(x).toFixed(d);
  const cards = [
    ['Cases', S.total_cases ?? 0, (S.flagged_cases ?? 0) + ' flagged'],
    ['Drift rate', pct(S.drift_rate), 'mean score ' + num(S.mean_drift_score,2)],
    ['Worst drift', num(S.max_drift_score,2), 'max per-case score'],
    ['Noise floor', num(Math.min(A.avg_self_similarity ?? 1, B.avg_self_similarity ?? 1),3), 'self-similarity'],
    ['Cost A → B', usd(A.avg_cost_usd) + ' → ' + usd(B.avg_cost_usd), 'avg per case'],
    ['p50 latency', ms(A.p50_latency_ms) + ' → ' + ms(B.p50_latency_ms), 'per sample'],
  ];
  document.getElementById('cards').innerHTML = cards.map(
    ([k,val,sub]) => `<div class="card"><div class="k">${esc(k)}</div>
      <div class="v">${esc(val)}</div><div class="sub">${esc(sub)}</div></div>`).join('');

  /* -- alerts --------------------------------------------------------- */
  const V = (D.verdict && D.verdict.violations) || [];
  const W = (D.verdict && D.verdict.warnings) || [];
  let alerts = '';
  if (V.length) alerts += `<h2>Threshold violations</h2><ul class="violations">` +
    V.map(x => `<li><code>${esc(x.rule)}</code> — ${esc(x.message)}</li>`).join('') + '</ul>';
  if (W.length) alerts += `<h2>Warnings</h2><ul class="warnings">` +
    W.map(x => `<li><code>${esc(x.rule)}</code> — ${esc(x.message)}</li>`).join('') + '</ul>';
  document.getElementById('alerts').innerHTML = alerts;

  /* -- suite table ---------------------------------------------------- */
  function delta(a,b,invert){
    if (a == null || b == null) return '<span class="flat">—</span>';
    const c = b - a;
    if (Math.abs(c) < 1e-12) return '<span class="flat">＝</span>';
    const good = invert ? c < 0 : c > 0;
    const cls = good ? 'down' : 'up';
    return `<span class="${cls}">${c > 0 ? '▲ +' : '▼ '}${c.toFixed(4)}</span>`;
  }
  const rows = [
    ['Schema-valid rate', pct(A.schema_valid_rate), pct(B.schema_valid_rate), delta(A.schema_valid_rate,B.schema_valid_rate)],
    ['Strict-JSON rate', pct(A.strict_json_rate), pct(B.strict_json_rate), delta(A.strict_json_rate,B.strict_json_rate)],
    ['Expected-hit rate', pct(A.expected_hit_rate), pct(B.expected_hit_rate), delta(A.expected_hit_rate,B.expected_hit_rate)],
    ['Expected-tool-hit', pct(A.expected_tool_hit_rate), pct(B.expected_tool_hit_rate), delta(A.expected_tool_hit_rate,B.expected_tool_hit_rate)],
    ['Tool-args valid', pct(A.tool_args_valid_rate), pct(B.tool_args_valid_rate), delta(A.tool_args_valid_rate,B.tool_args_valid_rate)],
    ['Refusal rate', pct(A.refusal_rate), pct(B.refusal_rate), delta(A.refusal_rate,B.refusal_rate,true)],
    ['Error rate', pct(A.error_rate), pct(B.error_rate), delta(A.error_rate,B.error_rate,true)],
    ['Truncated rate', pct(A.truncated_rate), pct(B.truncated_rate), delta(A.truncated_rate,B.truncated_rate,true)],
    ['Avg cost / case', usd(A.avg_cost_usd), usd(B.avg_cost_usd), delta(A.avg_cost_usd,B.avg_cost_usd,true)],
    ['Total cost', usd(A.total_cost_usd), usd(B.total_cost_usd), delta(A.total_cost_usd,B.total_cost_usd,true)],
    ['p50 latency', ms(A.p50_latency_ms), ms(B.p50_latency_ms), delta(A.p50_latency_ms,B.p50_latency_ms,true)],
    ['p95 latency', ms(A.p95_latency_ms), ms(B.p95_latency_ms), delta(A.p95_latency_ms,B.p95_latency_ms,true)],
    ['Self-similarity', num(A.avg_self_similarity,3), num(B.avg_self_similarity,3), '<span class="flat">—</span>'],
  ];
  document.querySelector('#suite-table tbody').innerHTML = rows.map(r =>
    `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td><td>${r[3]}</td></tr>`).join('');

  /* -- tag table ------------------------------------------------------ */
  const tags = S.tags || [];
  document.querySelector('#tag-table tbody').innerHTML = tags.length
    ? tags.map(t => {
        const hist = t.flag_histogram || {};
        const top = Object.keys(hist).sort((a,b)=>hist[b]-hist[a])[0] || '—';
        const color = t.drift_rate > 0.25 ? 'var(--fail)' : (t.drift_rate > 0 ? 'var(--warn)' : 'var(--pass)');
        return `<tr><td><span class="tag">${esc(t.tag)}</span></td><td>${t.cases}</td>
          <td>${t.flagged_cases}</td>
          <td style="color:${color};font-weight:700">${pct(t.drift_rate)}</td>
          <td><code>${esc(top)}</code></td></tr>`;
      }).join('')
    : '<tr><td colspan="5" class="empty">no tags in this suite</td></tr>';

  /* -- flag rules ----------------------------------------------------- */
  const rules = S.flag_rules || {};
  document.getElementById('cases-flags').innerHTML = Object.keys(S.flag_histogram||{}).length
    ? '<div class="detail" style="margin:-4px 0 10px">Flag rules: ' +
      Object.keys(S.flag_histogram).map(f => `<code>${esc(f)}</code>: ${esc(rules[f]||'')}`).join(' · ') +
      '</div>'
    : '';

  /* -- cases ---------------------------------------------------------- */
  const cases = D.cases || [];
  let activeFlags = new Set();
  let activeTags = new Set();

  const flagCounts = {};
  cases.forEach(c => (c.flags||[]).forEach(f => flagCounts[f] = (flagCounts[f]||0)+1));
  document.getElementById('flag-filters').innerHTML = Object.keys(flagCounts)
    .sort((a,b)=>flagCounts[b]-flagCounts[a])
    .map(f => `<span class="chip flag-chip" data-flag="${esc(f)}">${esc(f)} <span class="n">${flagCounts[f]}</span></span>`).join('');

  const tagCounts = {};
  cases.forEach(c => (c.tags||[]).forEach(t => tagCounts[t] = (tagCounts[t]||0)+1));
  document.getElementById('tag-filters').innerHTML = Object.keys(tagCounts)
    .sort((a,b)=>tagCounts[b]-tagCounts[a])
    .map(t => `<span class="chip tag-chip" data-tag="${esc(t)}">${esc(t)} <span class="n">${tagCounts[t]}</span></span>`).join('');

  function outBlock(side, sideKey, caseObj){
    const st = caseObj[side] || {};
    const samples = st.outputs || [];
    const sampleMetrics = st.sample_metrics || [];
    const spec = (side === 'a' ? (D.from && D.from.spec) : (D.to && D.to.spec)) || sideKey;
    const mech = st.structured_output_mechanism;
    const controls = samples.map((_,i) =>
      `<button data-sample="${i}" class="${i===0?'on':''}">#${i+1}</button>`).join('');
    const body = samples.map((s,i) =>
      `<div class="sample-body" data-sample="${i}" ${i===0?'':'style="display:none"'}>${esc(s) || '<span style="color:var(--muted)">(empty)</span>'}</div>`).join('');
    return `<div class="out-wrap" data-side="${sideKey}">
      <div class="out-head">
        <div><strong>Model ${sideKey.toUpperCase()}</strong>
          <span class="detail" style="font-family:ui-monospace,monospace">${esc(spec)}</span>
          ${mech ? `<span class="lang-badge">${esc(mech)}</span>` : ''}
        </div>
        <div class="samples">${controls}</div>
      </div>
      <div class="out">${body || '<span style="color:var(--muted)">(no output)</span>'}</div>
      <div class="metrics">${metricsFor(sampleMetrics[0] || {})}</div>
    </div>`;
  }

  function metricsFor(m){
    const items = [
      ['latency', ms(m.latency_ms)],
      ['tokens out', m.tokens_out ?? '—'],
      ['cost', usd(m.cost_usd)],
      ['chars', m.chars],
      ['refused', m.refused ? 'yes' : 'no'],
      ['strict json', m.strict_json == null ? '—' : (m.strict_json ? 'yes' : 'no')],
      ['schema ok', m.schema_valid == null ? '—' : (m.schema_valid ? 'yes' : 'no')],
      ['expected', m.expected_hit == null ? '—' : (m.expected_hit ? 'yes' : 'no')],
      ['tools', (m.tool_names && m.tool_names.length) ? m.tool_names.join(', ') : '—'],
      ['lang', m.language || '—'],
    ];
    return items.map(([k,val]) =>
      `<div class="metric"><div class="mk">${esc(k)}</div><div class="mv">${esc(val)}</div></div>`).join('');
  }

  function renderCases(){
    const q = (document.getElementById('search').value || '').toLowerCase();
    const onlyDrifted = document.getElementById('only-drifted').checked;
    const filtered = cases.filter(c => {
      if (onlyDrifted && !(c.flags && c.flags.length)) return false;
      if (activeFlags.size && !(c.flags||[]).some(f => activeFlags.has(f))) return false;
      if (activeTags.size && !(c.tags||[]).some(t => activeTags.has(t))) return false;
      if (q){
        const hay = (c.id + ' ' + (c.tags||[]).join(' ') + ' ' +
          JSON.stringify(c.a && c.a.outputs) + ' ' + JSON.stringify(c.b && c.b.outputs)).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    }).sort((x,y) => (y.drift_score||0) - (x.drift_score||0));

    const host = document.getElementById('cases');
    host.innerHTML = filtered.map(c => {
      const flags = (c.flags||[]).map(f => `<span class="flag">${esc(f)}</span>`).join('');
      const tagsHtml = (c.tags||[]).map(t => `<span class="tag">${esc(t)}</span>`).join('');
      const details = Object.keys(c.flag_details||{}).map(f =>
        `<div class="detail">• <code>${esc(f)}</code>: ${esc(c.flag_details[f])}</div>`).join('');
      const judge = c.judge
        ? `<div class="detail">⚖ judge: better=${esc(c.judge.better)}${c.judge.uncertain?' (uncertain)':''} — ${esc(c.judge.reason||'')}</div>` : '';
      return `<details class="case" ${c.flags && c.flags.length ? 'open' : ''}>
        <summary>
          <span class="case-id">${esc(c.id)}</span>
          ${c.drift_score ? `<span class="drift">drift ${Number(c.drift_score).toFixed(2)}</span>` : ''}
          ${flags}${tagsHtml}
        </summary>
        <div class="case-body">
          <div class="detail">cross-similarity ${num(c.cross_similarity,3)} · noise floor ${num(c.noise_floor,3)} · kind ${esc(c.kind||'—')}</div>
          ${details}${judge}
          <div class="grid2">${outBlock('a','a',c)}${outBlock('b','b',c)}</div>
        </div>
      </details>`;
    }).join('');

    document.getElementById('cases-empty').classList.toggle('hidden', filtered.length > 0);

    host.querySelectorAll('.out-wrap').forEach(wrap => {
      wrap.querySelectorAll('.samples button').forEach(btn => {
        btn.onclick = () => {
          const idx = btn.dataset.sample;
          wrap.querySelectorAll('.samples button').forEach(b => b.classList.toggle('on', b === btn));
          wrap.querySelectorAll('.sample-body').forEach(body =>
            body.style.display = body.dataset.sample === idx ? '' : 'none');
        };
      });
    });
  }

  document.querySelectorAll('.flag-chip').forEach(chip => {
    chip.onclick = () => {
      const f = chip.dataset.flag;
      if (activeFlags.has(f)) activeFlags.delete(f); else activeFlags.add(f);
      chip.classList.toggle('on');
      renderCases();
    };
  });
  document.querySelectorAll('.tag-chip').forEach(chip => {
    chip.onclick = () => {
      const t = chip.dataset.tag;
      if (activeTags.has(t)) activeTags.delete(t); else activeTags.add(t);
      chip.classList.toggle('on');
      renderCases();
    };
  });
  document.getElementById('search').oninput = renderCases;
  document.getElementById('only-drifted').onchange = renderCases;

  /* -- reproduce + meta ----------------------------------------------- */
  const cmd = (D.reproduce && D.reproduce.command) || 'modelbump diff ...';
  document.getElementById('repro-command').textContent = cmd;
  document.getElementById('copy').onclick = async () => {
    try { await navigator.clipboard.writeText(cmd);
      const b = document.getElementById('copy'); b.textContent = 'copied ✓';
      setTimeout(() => b.textContent = 'copy command', 1400);
    } catch(e){}
  };
  const R = D.reproduce || {};
  const meta = [
    ['modelbump', (D.modelbump||{}).version || '?'],
    ['report schema', D.schema_version || '?'],
    ['config hash', R.config_hash || '—'],
    ['rubric hash', D.rubric_hash || '—'],
    ['command hash', R.command_hash || '—'],
    ['cache', (R.cache_hits||0) + ' hits / ' + (R.cache_misses||0) + ' misses'],
    ['elapsed', R.elapsed_s != null ? R.elapsed_s + ' s' : '—'],
    ['structured A', (D.structured_output||{}).a || 'none'],
    ['structured B', (D.structured_output||{}).b || 'none'],
    ['registry', ((D.registry||{}).sources||[]).join(', ') || '—'],
  ];
  document.getElementById('meta-cards').innerHTML = meta.map(([k,val]) =>
    `<div class="card"><div class="k">${esc(k)}</div><div class="v" style="font-size:14px;word-break:break-all">${esc(val)}</div></div>`).join('');
  document.getElementById('foot-version').textContent = 'v' + ((D.modelbump||{}).version || '?');
  document.getElementById('foot-time').textContent = D.generated_at || '';

  renderCases();
})();
</script>
</body>
</html>
"""
