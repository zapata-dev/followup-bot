"""
Admin Dashboard — Self-contained HTML dashboard for campaign management.
Served as inline HTML from GET /admin.
"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Followup Bot — Panel Admin</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }

.header { background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border-bottom: 1px solid #334155; padding: 20px 32px; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 24px; font-weight: 700; }
.header h1 span { color: #22d3ee; }
.header-status { display: flex; gap: 16px; align-items: center; }
.status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.status-dot.ok { background: #22c55e; box-shadow: 0 0 8px #22c55e; }
.status-dot.err { background: #ef4444; box-shadow: 0 0 8px #ef4444; }

.container { max-width: 1100px; margin: 0 auto; padding: 32px 24px; }

/* Stats bar */
.stats-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 32px; }
.stat-card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; }
.stat-card .label { font-size: 12px; text-transform: uppercase; color: #94a3b8; letter-spacing: 1px; margin-bottom: 8px; }
.stat-card .value { font-size: 28px; font-weight: 700; color: #f8fafc; }
.stat-card .value.cyan { color: #22d3ee; }
.stat-card .value.green { color: #22c55e; }
.stat-card .value.yellow { color: #eab308; }

/* Campaigns section */
.section-title { font-size: 18px; font-weight: 600; margin-bottom: 16px; color: #f8fafc; }

.campaign-grid { display: flex; flex-direction: column; gap: 12px; }

.campaign-card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px 24px; display: flex; justify-content: space-between; align-items: center; transition: border-color 0.2s; }
.campaign-card:hover { border-color: #475569; }

.campaign-info { flex: 1; }
.campaign-info .name { font-size: 16px; font-weight: 600; color: #f8fafc; margin-bottom: 4px; }
.campaign-info .meta { font-size: 13px; color: #94a3b8; }
.campaign-info .meta span { margin-right: 16px; }
.campaign-info .id-badge { font-size: 11px; color: #64748b; font-family: monospace; background: #0f172a; padding: 2px 8px; border-radius: 4px; display: inline-block; margin-top: 4px; }

.campaign-actions { display: flex; gap: 10px; align-items: center; }

.btn { padding: 10px 24px; border-radius: 8px; border: none; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.2s; display: inline-flex; align-items: center; gap: 6px; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-start { background: #22c55e; color: #052e16; }
.btn-start:hover:not(:disabled) { background: #16a34a; transform: translateY(-1px); }
.btn-pause { background: #eab308; color: #422006; }
.btn-pause:hover:not(:disabled) { background: #ca8a04; }
.btn-refresh { background: #334155; color: #e2e8f0; }
.btn-refresh:hover { background: #475569; }

.campaign-status { font-size: 12px; font-weight: 600; padding: 4px 12px; border-radius: 20px; text-transform: uppercase; letter-spacing: 0.5px; }
.campaign-status.active { background: #052e16; color: #22c55e; border: 1px solid #22c55e; }
.campaign-status.paused { background: #422006; color: #eab308; border: 1px solid #eab308; }
.campaign-status.idle { background: #1e293b; color: #64748b; border: 1px solid #475569; }

/* Toast notifications */
.toast-container { position: fixed; top: 20px; right: 20px; z-index: 1000; display: flex; flex-direction: column; gap: 8px; }
.toast { padding: 12px 20px; border-radius: 8px; font-size: 14px; font-weight: 500; animation: slideIn 0.3s ease; max-width: 380px; }
.toast.success { background: #052e16; color: #22c55e; border: 1px solid #22c55e; }
.toast.error { background: #450a0a; color: #ef4444; border: 1px solid #ef4444; }
.toast.info { background: #0c1a3d; color: #3b82f6; border: 1px solid #3b82f6; }
@keyframes slideIn { from { transform: translateX(100px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }

/* Loading spinner */
.spinner { width: 18px; height: 18px; border: 2px solid transparent; border-top-color: currentColor; border-radius: 50%; animation: spin 0.8s linear infinite; display: inline-block; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Window indicator */
.window-badge { font-size: 12px; padding: 4px 12px; border-radius: 20px; font-weight: 600; }
.window-badge.open { background: #052e16; color: #22c55e; border: 1px solid #22c55e; }
.window-badge.closed { background: #450a0a; color: #ef4444; border: 1px solid #ef4444; }

/* Empty state */
.empty-state { text-align: center; padding: 60px 20px; color: #64748b; }
.empty-state .icon { font-size: 48px; margin-bottom: 16px; }
.empty-state p { font-size: 16px; }

/* Log area */
.log-section { margin-top: 32px; }
.log-box { background: #0f172a; border: 1px solid #334155; border-radius: 12px; padding: 16px; max-height: 250px; overflow-y: auto; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; line-height: 1.8; color: #94a3b8; }
.log-box .log-entry { border-bottom: 1px solid #1e293b; padding: 4px 0; }
.log-box .log-entry:last-child { border-bottom: none; }
.log-time { color: #475569; }
.log-success { color: #22c55e; }
.log-error { color: #ef4444; }
.log-info { color: #3b82f6; }
</style>
</head>
<body>

<div class="header">
    <h1>Followup <span>Bot</span></h1>
    <div class="header-status">
        <span class="window-badge" id="windowBadge">--</span>
        <span><span class="status-dot" id="healthDot"></span> <span id="healthText">Conectando...</span></span>
    </div>
</div>

<div class="container">
    <!-- Stats -->
    <div class="stats-bar">
        <div class="stat-card">
            <div class="label">Enviados esta hora</div>
            <div class="value cyan" id="sendsHour">--</div>
        </div>
        <div class="stat-card">
            <div class="label">Limite por hora</div>
            <div class="value" id="maxHour">--</div>
        </div>
        <div class="stat-card">
            <div class="label">Campanas activas</div>
            <div class="value green" id="activeCampaigns">--</div>
        </div>
        <div class="stat-card">
            <div class="label">Uptime</div>
            <div class="value" id="uptime">--</div>
        </div>
    </div>

    <!-- Campaigns -->
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <div class="section-title" style="margin-bottom: 0;">Campanas</div>
        <button class="btn btn-refresh" onclick="loadAll()">Actualizar</button>
    </div>

    <div class="campaign-grid" id="campaignGrid">
        <div class="empty-state">
            <div class="icon">...</div>
            <p>Cargando campanas...</p>
        </div>
    </div>

    <!-- Activity log -->
    <div class="log-section">
        <div class="section-title">Actividad</div>
        <div class="log-box" id="logBox">
            <div class="log-entry"><span class="log-time">[--:--]</span> <span class="log-info">Panel iniciado, conectando con el bot...</span></div>
        </div>
    </div>
</div>

<div class="toast-container" id="toastContainer"></div>

<script>
const BASE = window.location.origin;
let senderStatus = {};
let groups = [];

// ── Helpers ──
function toast(msg, type = 'info') {
    const c = document.getElementById('toastContainer');
    const t = document.createElement('div');
    t.className = 'toast ' + type;
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(() => t.remove(), 4000);
}

function addLog(msg, cls = 'log-info') {
    const box = document.getElementById('logBox');
    const now = new Date().toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.innerHTML = '<span class="log-time">[' + now + ']</span> <span class="' + cls + '">' + msg + '</span>';
    box.prepend(entry);
    // Keep max 50 entries
    while (box.children.length > 50) box.removeChild(box.lastChild);
}

function formatUptime(seconds) {
    if (!seconds) return '--';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (h > 0) return h + 'h ' + m + 'm';
    return m + 'm';
}

// ── API Calls ──
async function fetchJSON(url, opts = {}) {
    const r = await fetch(url, opts);
    return r.json();
}

async function loadHealth() {
    try {
        const d = await fetchJSON(BASE + '/health');
        document.getElementById('healthDot').className = 'status-dot ok';
        document.getElementById('healthText').textContent = d.company + ' — Online';
        document.getElementById('uptime').textContent = formatUptime(d.uptime_seconds);
        return d;
    } catch (e) {
        document.getElementById('healthDot').className = 'status-dot err';
        document.getElementById('healthText').textContent = 'Sin conexion';
        return null;
    }
}

async function loadStatus() {
    try {
        const d = await fetchJSON(BASE + '/admin/status');
        senderStatus = d;
        document.getElementById('sendsHour').textContent = d.sends_this_hour || 0;
        document.getElementById('maxHour').textContent = d.max_per_hour || '--';
        document.getElementById('activeCampaigns').textContent = Object.keys(d.active_campaigns || {}).length;

        const wb = document.getElementById('windowBadge');
        if (d.is_within_window) {
            wb.textContent = 'Ventana abierta';
            wb.className = 'window-badge open';
        } else {
            wb.textContent = 'Fuera de horario';
            wb.className = 'window-badge closed';
        }
        return d;
    } catch (e) {
        return null;
    }
}

async function loadGroups() {
    try {
        const d = await fetchJSON(BASE + '/admin/groups');
        groups = d.groups || [];
        renderCampaigns();
    } catch (e) {
        document.getElementById('campaignGrid').innerHTML = '<div class="empty-state"><p>Error cargando campanas</p></div>';
    }
}

function renderCampaigns() {
    const grid = document.getElementById('campaignGrid');

    if (!groups.length) {
        grid.innerHTML = '<div class="empty-state"><div class="icon">Vacio</div><p>No hay grupos en el tablero de Monday</p></div>';
        return;
    }

    grid.innerHTML = groups.map(g => {
        const isActive = senderStatus.active_campaigns && senderStatus.active_campaigns[g.id];
        const isPaused = senderStatus.paused_campaigns && senderStatus.paused_campaigns.includes(g.id);

        let statusHTML, actionsHTML;

        if (isActive) {
            statusHTML = '<span class="campaign-status active">Enviando</span>';
            actionsHTML = '<button class="btn btn-pause" onclick="pauseCampaign(\'' + g.id + '\')">Pausar</button>';
        } else if (isPaused) {
            statusHTML = '<span class="campaign-status paused">Pausada</span>';
            actionsHTML = '<button class="btn btn-start" onclick="startCampaign(\'' + g.id + '\')">Reanudar</button>';
        } else {
            statusHTML = '<span class="campaign-status idle">Lista</span>';
            actionsHTML = '<button class="btn btn-start" onclick="startCampaign(\'' + g.id + '\')">Iniciar Envio</button>';
        }

        const contactCount = g.pending_count !== undefined ? g.pending_count + ' pendientes' : '';

        return '<div class="campaign-card" id="card-' + g.id + '">'
            + '<div class="campaign-info">'
            + '<div class="name">' + g.title + '</div>'
            + '<div class="meta"><span>' + contactCount + '</span></div>'
            + '<div class="id-badge">' + g.id + '</div>'
            + '</div>'
            + '<div class="campaign-actions">'
            + statusHTML + ' '
            + actionsHTML
            + '</div>'
            + '</div>';
    }).join('');
}

async function startCampaign(groupId) {
    const group = groups.find(g => g.id === groupId);
    const name = group ? group.title : groupId;

    // Disable button while running
    const card = document.getElementById('card-' + groupId);
    if (card) {
        const btn = card.querySelector('.btn-start');
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Iniciando...'; }
    }

    try {
        const d = await fetchJSON(BASE + '/admin/start/' + groupId, { method: 'POST' });

        if (d.status === 'started') {
            toast('Campana iniciada: ' + name, 'success');
            addLog('Campana "' + name + '" iniciada', 'log-success');
        } else if (d.status === 'already_running') {
            toast('Esta campana ya esta corriendo', 'info');
            addLog('Campana "' + name + '" ya estaba activa', 'log-info');
        } else if (d.error) {
            toast('Error: ' + d.error, 'error');
            addLog('Error: ' + d.error, 'log-error');
        }

        // Refresh status after a moment
        setTimeout(loadAll, 1500);
    } catch (e) {
        toast('Error de conexion', 'error');
        addLog('Error de conexion al iniciar campana', 'log-error');
    }
}

async function pauseCampaign(groupId) {
    const group = groups.find(g => g.id === groupId);
    const name = group ? group.title : groupId;

    try {
        const d = await fetchJSON(BASE + '/admin/pause/' + groupId, { method: 'POST' });
        toast('Campana pausada: ' + name, 'info');
        addLog('Campana "' + name + '" pausada', 'log-info');
        setTimeout(loadAll, 1000);
    } catch (e) {
        toast('Error pausando campana', 'error');
    }
}

async function loadAll() {
    await Promise.all([loadHealth(), loadStatus(), loadGroups()]);
}

// ── Init ──
loadAll();
addLog('Dashboard conectado', 'log-success');

// Auto-refresh every 15 seconds
setInterval(loadAll, 15000);
</script>

</body>
</html>"""
