// ── All modal + popup functions defined first ─────────────

    function openModal(id) {
        const modal = document.getElementById(id);
        if (!modal) { console.warn('Modal not found:', id); return; }
        modal.classList.add('modal-open');
        document.body.style.overflow = 'hidden';
        modal.scrollTop = 0;
        if (id === 'modal-tracker') { try { refreshTrackerModal(); } catch(e) { console.warn(e); } }
    }
    function closeModal(id) {
        const modal = document.getElementById(id);
        if (!modal) return;
        modal.classList.remove('modal-open');
        document.body.style.overflow = '';
    }
    function showInfo(card) {
        const title = card.querySelector('.info-card-front').textContent.trim();
        const body  = card.querySelector('.info-card-data').textContent.trim();
        document.getElementById('info-popup-title').textContent = title;
        document.getElementById('info-popup-body').innerHTML = body.replace(/
/g, '<br>');
        document.getElementById('info-popup-backdrop').classList.add('open');
        document.body.style.overflow = 'hidden';
    }
    function closeInfo(e) {
        if (e.target === document.getElementById('info-popup-backdrop')) {
            document.getElementById('info-popup-backdrop').classList.remove('open');
            document.body.style.overflow = '';
        }
    }
    function closeInfoBtn() {
        document.getElementById('info-popup-backdrop').classList.remove('open');
        document.body.style.overflow = '';
    }

    // ── Global event handlers ─────────────────────────────────
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape') return;
        closeInfoBtn();
        document.querySelectorAll('.modal-backdrop.modal-open').forEach(m => {
            m.classList.remove('modal-open');
        });
        document.body.style.overflow = '';
    });
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('modal-backdrop')) {
            e.target.classList.remove('modal-open');
            document.body.style.overflow = '';
        }
    });

    if ('serviceWorker' in navigator) { navigator.serviceWorker.register('./sw.js'); }

    // ── Supabase config ───────────────────────────────────────
    const SB_URL = 'https://hpoxotxejiilxzhxiuan.supabase.co';
    const SB_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imhwb3hvdHhlamlpbHh6aHhpdWFuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk0Nzg2MzgsImV4cCI6MjA5NTA1NDYzOH0.57oQLnh3Wv8n1F34OVsNvFdsklVktbKUeTlGDkq1X7s';
    const TODAY  = new Date().toISOString().slice(0, 10);

    async function sbFetch(path, opts = {}) {
        try {
            const res = await fetch(SB_URL + '/rest/v1/' + path, {
                headers: {
                    'apikey':        SB_KEY,
                    'Authorization': 'Bearer ' + SB_KEY,
                    'Content-Type':  'application/json',
                    'Prefer':        opts.prefer || 'return=representation',
                },
                method: opts.method || 'GET',
                body:   opts.body   || undefined,
            });
            if (!res.ok) { console.error('Supabase', res.status, await res.text()); return null; }
            const text = await res.text();
            return text ? JSON.parse(text) : [];
        } catch(e) { console.error('Supabase fetch error', e); return null; }
    }

    async function logPickBtn(btn) {
        const pid    = btn.dataset.pid;
        const pname  = btn.dataset.pname;
        const conf   = btn.dataset.conf;
        const match  = btn.dataset.match;
        const score  = parseFloat(btn.dataset.score);
        const suffix = btn.dataset.suffix;
        await logPick(pid, pname, conf, match, score, suffix);
    }

    async function logPick(playerId, playerName, conf, matchup, score, suffix) {
        const nameEl = document.getElementById('pn-' + playerId + '-' + suffix);
        const lineEl = document.getElementById('pl-' + playerId + '-' + suffix);
        const msgEl  = document.getElementById('ps-' + playerId + '-' + suffix);
        const who    = nameEl ? nameEl.value.trim() : '';
        const line   = lineEl ? lineEl.value : 'Over 1.5';
        if (!who) {
            if (nameEl) { nameEl.style.borderColor = '#ff4444'; nameEl.focus(); }
            return;
        }
        if (nameEl) nameEl.style.borderColor = '';
        const existing = await sbFetch(
            'picks?who=eq.' + encodeURIComponent(who) +
            '&player_id=eq.' + encodeURIComponent(playerId) +
            '&date=eq.' + TODAY
        );
        if (existing && existing.length > 0) {
            if (msgEl) { msgEl.textContent = 'Already logged for ' + who + ' today!'; msgEl.classList.add('show'); }
            return;
        }
        const row = {
            date: TODAY, who, player_id: playerId, player_name: playerName,
            line, conf, matchup, score, result: 'pending', actual: null
        };
        const res = await sbFetch('picks', { method: 'POST', body: JSON.stringify(row) });
        if (!res) {
            if (msgEl) { msgEl.style.color = '#ff4444'; msgEl.textContent = 'Save failed — check console.'; msgEl.classList.add('show'); }
            return;
        }
        if (msgEl) { msgEl.style.color = '#00cc66'; msgEl.textContent = '✓ Logged: ' + who + ' on ' + playerName + ' ' + line; msgEl.classList.add('show'); }
        updateFabCount();
        refreshTrackerModal();
    }

    async function setResult(pickId, result) {
        await sbFetch('picks?id=eq.' + pickId, { method: 'PATCH', body: JSON.stringify({ result }), prefer: 'return=minimal' });
        refreshTrackerModal();
    }

    async function setActual(pickId, val) {
        await sbFetch('picks?id=eq.' + pickId, {
            method: 'PATCH',
            body:   JSON.stringify({ actual: val === '' ? null : parseInt(val) }),
            prefer: 'return=minimal'
        });
    }

    async function updateFabCount() {
        const picks = await sbFetch('picks?date=eq.' + TODAY + '&select=id');
        const el = document.getElementById('fab-count');
        if (el) el.textContent = picks ? picks.length : 0;
    }

    async function refreshTrackerModal() {
        const todayPicks = await sbFetch('picks?date=eq.' + TODAY + '&order=id.asc') || [];
        const allPicks   = await sbFetch('picks?order=id.asc') || [];
        const settled    = allPicks.filter(p => p.result !== 'pending');
        const hits       = settled.filter(p => p.result === 'hit');
        const hitRate    = settled.length ? Math.round((hits.length / settled.length) * 100) : null;

        document.getElementById('tr-total').textContent = allPicks.length;
        document.getElementById('tr-today').textContent = todayPicks.length;
        const rEl = document.getElementById('tr-rate');
        rEl.textContent = hitRate !== null ? hitRate + '%' : '—';
        rEl.style.color = hitRate >= 55 ? '#00ff88' : hitRate !== null && hitRate < 45 ? '#ff4444' : '#aabbcc';
        document.getElementById('tr-hits').textContent = hits.length;
        document.getElementById('tr-miss').textContent = settled.length - hits.length;

        const tbody = document.getElementById('tr-picks-body');
        if (!todayPicks.length) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#334455;padding:20px;font-style:italic">No picks logged today yet.</td></tr>';
        } else {
            tbody.innerHTML = todayPicks.map(p => {
                const rHit  = p.result === 'hit'  ? 'active' : '';
                const rMiss = p.result === 'miss' ? 'active' : '';
                return '<tr>' +
                    '<td style="font-weight:700;color:#ddeeff">' + p.who + '</td>' +
                    '<td>' + p.player_name + '</td>' +
                    '<td style="white-space:nowrap">' + p.line + '</td>' +
                    '<td><span style="font-size:11px;padding:2px 7px;border-radius:20px;background:#0a1020;color:#aabbcc">' + p.conf + '</span></td>' +
                    '<td><input type="number" min="0" max="10" placeholder="H+R+RBI" ' +
                    'value="' + (p.actual !== null && p.actual !== undefined ? p.actual : '') + '" ' +
                    'onchange="setActual(' + p.id + ', this.value)" ' +
                    'style="width:90px;background:#0d1525;border:1px solid #1a2a40;border-radius:5px;color:#ddeeff;font-size:12px;padding:3px 7px;font-family:inherit"></td>' +
                    '<td style="white-space:nowrap">' +
                    '<button class="result-btn hit '  + rHit  + '" data-id="' + p.id + '" data-r="hit"  onclick="setResult(this.dataset.id, this.dataset.r)">Hit</button>' +
                    '<button class="result-btn miss ' + rMiss + '" data-id="' + p.id + '" data-r="miss" onclick="setResult(this.dataset.id, this.dataset.r)">Miss</button>' +
                    '</td></tr>';
            }).join('');
        }

        const byWho = {};
        allPicks.forEach(p => {
            if (!byWho[p.who]) byWho[p.who] = { hits: 0, miss: 0, pend: 0 };
            if (p.result === 'hit') byWho[p.who].hits++;
            else if (p.result === 'miss') byWho[p.who].miss++;
            else byWho[p.who].pend++;
        });
        const board = Object.entries(byWho)
            .map(([name, s]) => {
                const tot = s.hits + s.miss;
                return { name, ...s, rate: tot ? Math.round((s.hits / tot) * 100) : null };
            })
            .sort((a, b) => (b.rate ?? -1) - (a.rate ?? -1));

        const medals = ['1st','2nd','3rd'];
        const lb = document.getElementById('tr-leaderboard');
        lb.innerHTML = !board.length
            ? '<div style="color:#334455;text-align:center;padding:16px;font-style:italic">No data yet.</div>'
            : board.map((r, i) => {
                const rc = r.rate >= 55 ? '#00ff88' : r.rate !== null && r.rate < 45 ? '#ff4444' : '#aabbcc';
                return '<div class="leaderboard-row">' +
                    '<div class="lb-medal">' + (i < 3 ? medals[i] : i + 1) + '</div>' +
                    '<div class="lb-name">' + r.name + '</div>' +
                    '<div class="lb-stats"><div class="lb-rate" style="color:' + rc + '">' +
                    (r.rate !== null ? r.rate + '%' : '—') + '</div>' +
                    r.hits + 'W · ' + r.miss + 'L · ' + r.pend + ' pending</div></div>';
            }).join('');

        updateFabCount();
    }

    // ── Init ──────────────────────────────────────────────────
    (async () => { try { await updateFabCount(); } catch(e) { console.warn('FAB init:', e); } })();