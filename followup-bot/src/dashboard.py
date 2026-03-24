"""
Admin Dashboard — Self-contained HTML dashboard for campaign management.
Served as inline HTML from GET /admin.
"""

DASHBOARD_HTML = r"""<!DOCTYPE html>
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

/* Tab navigation */
.tab-nav { display: flex; gap: 0; margin-bottom: 32px; border-bottom: 2px solid #334155; }
.tab-btn { padding: 12px 28px; font-size: 15px; font-weight: 600; color: #64748b; background: none; border: none; cursor: pointer; transition: all 0.2s; position: relative; }
.tab-btn:hover { color: #e2e8f0; }
.tab-btn.active { color: #22d3ee; }
.tab-btn.active::after { content: ''; position: absolute; bottom: -2px; left: 0; right: 0; height: 2px; background: #22d3ee; }
.tab-content { display: none; }
.tab-content.active { display: block; }

/* Template Builder */
.tb-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
@media (max-width: 768px) { .tb-grid { grid-template-columns: 1fr; } }

.tb-panel { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 24px; }
.tb-panel h3 { font-size: 15px; font-weight: 600; color: #f8fafc; margin-bottom: 16px; }

.tb-label { font-size: 12px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.5px; margin-bottom: 6px; display: block; }
.tb-input { width: 100%; padding: 10px 14px; border-radius: 8px; border: 1px solid #334155; background: #0f172a; color: #e2e8f0; font-size: 14px; font-family: inherit; outline: none; transition: border-color 0.2s; }
.tb-input:focus { border-color: #22d3ee; }
.tb-input::placeholder { color: #475569; }
.tb-field { margin-bottom: 14px; }

.tb-textarea { width: 100%; min-height: 140px; padding: 14px; border-radius: 8px; border: 1px solid #334155; background: #0f172a; color: #e2e8f0; font-size: 14px; font-family: 'SF Mono', 'Fira Code', monospace; line-height: 1.6; outline: none; resize: vertical; transition: border-color 0.2s; }
.tb-textarea:focus { border-color: #22d3ee; }
.tb-textarea::placeholder { color: #475569; }

.tb-actions { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
.btn-preview { background: #22d3ee; color: #0f172a; }
.btn-preview:hover { background: #06b6d4; transform: translateY(-1px); }
.btn-regen { background: #8b5cf6; color: #fff; }
.btn-regen:hover { background: #7c3aed; transform: translateY(-1px); }
.btn-copy { background: #334155; color: #e2e8f0; }
.btn-copy:hover { background: #475569; }

.tb-preview-box { background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 16px; min-height: 100px; font-size: 14px; line-height: 1.7; color: #f8fafc; white-space: pre-wrap; word-wrap: break-word; }
.tb-preview-box.empty { color: #475569; font-style: italic; }

.tb-validation { margin-top: 12px; padding: 10px 14px; border-radius: 8px; font-size: 13px; font-weight: 500; display: none; }
.tb-validation.warning { display: block; background: #422006; color: #eab308; border: 1px solid #eab308; }
.tb-validation.ok { display: block; background: #052e16; color: #22c55e; border: 1px solid #22c55e; }

/* Variable chips */
.tb-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.tb-chip { font-size: 12px; padding: 4px 10px; border-radius: 6px; background: #0f172a; border: 1px solid #334155; color: #94a3b8; cursor: pointer; transition: all 0.2s; font-family: 'SF Mono', 'Fira Code', monospace; }
.tb-chip:hover { border-color: #22d3ee; color: #22d3ee; }

/* Spintax examples */
.tb-example { background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 12px; margin-top: 10px; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; line-height: 1.8; color: #94a3b8; cursor: pointer; transition: border-color 0.2s; }
.tb-example:hover { border-color: #22d3ee; }
.tb-example-label { font-size: 11px; text-transform: uppercase; color: #64748b; letter-spacing: 0.5px; margin-bottom: 4px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }

/* Counter */
.tb-counter { font-size: 12px; color: #64748b; margin-top: 8px; }
.tb-counter strong { color: #22d3ee; }

/* ── V2: Quality Semaphore ── */
.tb-semaphore { display: flex; align-items: center; gap: 12px; padding: 14px 18px; border-radius: 10px; margin-bottom: 16px; border: 1px solid; }
.tb-semaphore.green { background: #052e16; border-color: #22c55e; }
.tb-semaphore.yellow { background: #422006; border-color: #eab308; }
.tb-semaphore.red { background: #450a0a; border-color: #ef4444; }
.tb-sem-dot { width: 14px; height: 14px; border-radius: 50%; flex-shrink: 0; }
.tb-semaphore.green .tb-sem-dot { background: #22c55e; box-shadow: 0 0 8px #22c55e; }
.tb-semaphore.yellow .tb-sem-dot { background: #eab308; box-shadow: 0 0 8px #eab308; }
.tb-semaphore.red .tb-sem-dot { background: #ef4444; box-shadow: 0 0 8px #ef4444; }
.tb-sem-text { font-size: 13px; font-weight: 600; }
.tb-semaphore.green .tb-sem-text { color: #22c55e; }
.tb-semaphore.yellow .tb-sem-text { color: #eab308; }
.tb-semaphore.red .tb-sem-text { color: #ef4444; }

/* Quality checks list */
.tb-checks { list-style: none; padding: 0; margin: 10px 0 0 0; }
.tb-checks li { font-size: 12px; padding: 3px 0; display: flex; align-items: center; gap: 8px; }
.tb-check-ok { color: #22c55e; }
.tb-check-warn { color: #eab308; }
.tb-check-fail { color: #ef4444; }

/* ── V2: WhatsApp Bubble Preview ── */
.tb-wa-container { display: flex; flex-direction: column; gap: 10px; }
.tb-wa-bubble { background: #005c4b; border-radius: 8px 8px 8px 0; padding: 10px 14px; font-size: 14px; line-height: 1.6; color: #e9edef; max-width: 100%; white-space: pre-wrap; word-wrap: break-word; position: relative; box-shadow: 0 1px 2px rgba(0,0,0,0.3); }
.tb-wa-bubble .tb-wa-time { display: block; text-align: right; font-size: 11px; color: #8696a0; margin-top: 4px; }
.tb-wa-bubble .tb-wa-num { position: absolute; top: -18px; left: 0; font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }
.tb-wa-bubble .tb-wa-injected { color: #fbbf24; font-style: italic; }

/* ── V2: Stats bar for builder ── */
.tb-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 16px; }
@media (max-width: 768px) { .tb-stats { grid-template-columns: repeat(2, 1fr); } }
.tb-stat { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 12px; text-align: center; }
.tb-stat .tb-stat-val { font-size: 22px; font-weight: 700; color: #f8fafc; }
.tb-stat .tb-stat-label { font-size: 11px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }
.tb-stat.warn .tb-stat-val { color: #eab308; }
.tb-stat.danger .tb-stat-val { color: #ef4444; }

/* ── V2: Inject alert ── */
.tb-inject-alert { padding: 10px 14px; border-radius: 8px; font-size: 12px; background: #1e1b4b; color: #a78bfa; border: 1px solid #7c3aed; margin-bottom: 12px; display: none; }
.tb-inject-alert.visible { display: block; }

/* Button gen5 */
.btn-gen5 { background: #f97316; color: #fff; }
.btn-gen5:hover { background: #ea580c; transform: translateY(-1px); }
.btn-copy-preview { background: #0ea5e9; color: #fff; }
.btn-copy-preview:hover { background: #0284c7; transform: translateY(-1px); }

/* ── V3: Campaign selector ── */
.tb-campaign-select { width: 100%; padding: 10px 14px; border-radius: 8px; border: 1px solid #334155; background: #0f172a; color: #e2e8f0; font-size: 14px; outline: none; cursor: pointer; appearance: none; -webkit-appearance: none; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='%2394a3b8' viewBox='0 0 16 16'%3E%3Cpath d='M8 11L3 6h10z'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 14px center; padding-right: 36px; }
.tb-campaign-select:focus { border-color: #22d3ee; }
.tb-campaign-hint { font-size: 12px; color: #64748b; margin-top: 6px; font-style: italic; }

/* ── V3: Snippet library ── */
.tb-snippet-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-top: 8px; }
@media (max-width: 768px) { .tb-snippet-grid { grid-template-columns: 1fr; } }
.tb-snippet { font-size: 12px; padding: 8px 10px; border-radius: 6px; background: #0f172a; border: 1px solid #334155; color: #94a3b8; cursor: pointer; transition: all 0.2s; text-align: left; font-family: inherit; line-height: 1.4; }
.tb-snippet:hover { border-color: #22d3ee; color: #22d3ee; }
.tb-snippet-cat { font-size: 11px; text-transform: uppercase; color: #64748b; letter-spacing: 0.5px; margin-top: 12px; margin-bottom: 6px; font-weight: 600; }
.tb-snippet-cat:first-child { margin-top: 0; }

/* ── V3: Good vs Bad examples ── */
.tb-compare { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 10px; }
@media (max-width: 768px) { .tb-compare { grid-template-columns: 1fr; } }
.tb-compare-card { border-radius: 8px; padding: 12px; font-size: 12px; line-height: 1.6; }
.tb-compare-card.good { background: #052e16; border: 1px solid #22c55e; color: #bbf7d0; }
.tb-compare-card.bad { background: #450a0a; border: 1px solid #ef4444; color: #fecaca; }
.tb-compare-label { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; font-weight: 700; margin-bottom: 6px; }
.tb-compare-card.good .tb-compare-label { color: #22c55e; }
.tb-compare-card.bad .tb-compare-label { color: #ef4444; }
.tb-compare-why { font-size: 11px; margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(255,255,255,0.1); font-style: italic; color: #94a3b8; }
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
    <!-- Tab Navigation -->
    <div class="tab-nav">
        <button class="tab-btn active" onclick="switchTab('campaigns')">Campanas</button>
        <button class="tab-btn" onclick="switchTab('builder')">Constructor de Templates</button>
        <button class="tab-btn" onclick="switchTab('csvgen')">Generador CSV</button>
    </div>

    <!-- ═══════════ TAB 1: CAMPAIGNS ═══════════ -->
    <div class="tab-content active" id="tab-campaigns">
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

    <!-- ═══════════ TAB 2: TEMPLATE BUILDER ═══════════ -->
    <div class="tab-content" id="tab-builder">

        <div class="tb-grid">
            <!-- LEFT COLUMN: Editor -->
            <div>
                <!-- Campaign selector -->
                <div class="tb-panel" style="margin-bottom: 16px;">
                    <h3>Tipo de campana</h3>
                    <select class="tb-campaign-select" id="tbCampaignType" onchange="tbCampaignChanged()">
                        <option value="">-- Seleccionar tipo --</option>
                        <option value="lost_lead">Lead perdido / Sin interes</option>
                        <option value="assigned_lead">Lead asignado / Cotizacion</option>
                        <option value="attended_appointment">Cita atendida / Post-visita</option>
                        <option value="customer_service">Servicio / Postventa</option>
                    </select>
                    <div class="tb-campaign-hint" id="tbCampaignHint">Selecciona un tipo para cargar sugerencias y tono recomendado</div>
                </div>

                <!-- Test data -->
                <div class="tb-panel" style="margin-bottom: 16px;">
                    <h3>Datos de prueba</h3>
                    <div class="tb-field">
                        <label class="tb-label">Nombre del contacto</label>
                        <input class="tb-input" id="tbNombre" type="text" placeholder="Carlos Mendoza" value="Carlos Mendoza">
                    </div>
                    <div class="tb-field">
                        <label class="tb-label">Vehiculo de interes</label>
                        <input class="tb-input" id="tbVehiculo" type="text" placeholder="Freightliner Cascadia 2020" value="Freightliner Cascadia 2020">
                    </div>
                    <div class="tb-field">
                        <label class="tb-label">Notas (opcional)</label>
                        <input class="tb-input" id="tbNotas" type="text" placeholder="Interesado en financiamiento">
                    </div>
                    <div class="tb-field" style="margin-bottom: 0;">
                        <label class="tb-label">Resumen conversacion previa (opcional)</label>
                        <input class="tb-input" id="tbResumen" type="text" placeholder="Pregunto precio y disponibilidad">
                    </div>
                </div>

                <!-- Template editor -->
                <div class="tb-panel">
                    <h3>Template</h3>
                    <textarea class="tb-textarea" id="tbTemplate" placeholder="Escribe tu template aqui...&#10;&#10;Usa variables: {nombre}, {vehiculo}&#10;Usa spintax: [opcion1|opcion2|opcion3]">[Hola|Buenas|Que tal] {nombre}, vi que estuviste viendo el {vehiculo}.
¿[Sigues interesado|Todavia lo evaluas|Ya resolviste algo]?</textarea>

                    <div class="tb-validation" id="tbValidation"></div>

                    <div class="tb-counter" id="tbCounter"></div>

                    <div class="tb-actions">
                        <button class="btn btn-preview" onclick="tbPreview()">Vista Previa</button>
                        <button class="btn btn-gen5" onclick="tbGenerate5()">Generar 5 Variantes</button>
                        <button class="btn btn-copy" onclick="tbCopyTemplate()">Copiar Template</button>
                        <button class="btn btn-copy-preview" onclick="tbCopyPreview()">Copiar Preview</button>
                    </div>
                </div>
            </div>

            <!-- RIGHT COLUMN: Preview + Quality + Reference -->
            <div>
                <!-- Quality semaphore -->
                <div class="tb-semaphore green" id="tbSemaphore" style="display: none;">
                    <div class="tb-sem-dot"></div>
                    <div>
                        <div class="tb-sem-text" id="tbSemText">Listo para usar</div>
                        <ul class="tb-checks" id="tbChecks"></ul>
                    </div>
                </div>

                <!-- Stats bar -->
                <div class="tb-stats" id="tbStatsBar" style="display: none;">
                    <div class="tb-stat" id="tbStatChars">
                        <div class="tb-stat-val">0</div>
                        <div class="tb-stat-label">Caracteres</div>
                    </div>
                    <div class="tb-stat" id="tbStatWords">
                        <div class="tb-stat-val">0</div>
                        <div class="tb-stat-label">Palabras</div>
                    </div>
                    <div class="tb-stat" id="tbStatLines">
                        <div class="tb-stat-val">0</div>
                        <div class="tb-stat-label">Lineas</div>
                    </div>
                    <div class="tb-stat" id="tbStatCombos">
                        <div class="tb-stat-val">0</div>
                        <div class="tb-stat-label">Variantes</div>
                    </div>
                </div>

                <!-- Inject alert -->
                <div class="tb-inject-alert" id="tbInjectAlert">
                    El template no incluye presentacion del bot. El sistema agregara automaticamente:<br>
                    <strong>"Hola, soy Estefania Fernandez de Go-On Zapata."</strong> al inicio del mensaje.
                </div>

                <!-- WhatsApp Preview -->
                <div class="tb-panel" style="margin-bottom: 16px;">
                    <h3>Vista previa (como se ve en WhatsApp)</h3>
                    <div class="tb-wa-container" id="tbWaContainer">
                        <div style="text-align: center; padding: 30px; color: #475569; font-style: italic; font-size: 13px;">
                            Haz clic en "Vista Previa" o "Generar 5 Variantes" para ver los mensajes...
                        </div>
                    </div>
                </div>

                <!-- Quick reference: Variables -->
                <div class="tb-panel" style="margin-bottom: 16px;">
                    <h3>Variables disponibles</h3>
                    <p style="font-size: 12px; color: #94a3b8; margin-bottom: 10px;">Clic para insertar en el template</p>
                    <div class="tb-chips">
                        <span class="tb-chip" onclick="tbInsertVar('{nombre}')">{nombre}</span>
                        <span class="tb-chip" onclick="tbInsertVar('{vehiculo}')">{vehiculo}</span>
                        <span class="tb-chip" onclick="tbInsertVar('{bot_name}')">{bot_name}</span>
                        <span class="tb-chip" onclick="tbInsertVar('{company_name}')">{company_name}</span>
                        <span class="tb-chip" onclick="tbInsertVar('{company_url}')">{company_url}</span>
                        <span class="tb-chip" onclick="tbInsertVar('{notas}')">{notas}</span>
                        <span class="tb-chip" onclick="tbInsertVar('{resumen}')">{resumen}</span>
                    </div>
                </div>

                <!-- Snippet library -->
                <div class="tb-panel" style="margin-bottom: 16px;">
                    <h3>Biblioteca de bloques</h3>
                    <p style="font-size: 12px; color: #94a3b8; margin-bottom: 6px;">Clic en un bloque para insertarlo en el template. Arma tu mensaje como LEGO.</p>

                    <div class="tb-snippet-cat">Saludos</div>
                    <div class="tb-snippet-grid">
                        <button class="tb-snippet" onclick="tbInsertSnippet('[Hola|Buenas|Que tal] {nombre}')">Informal: [Hola|Buenas|Que tal]</button>
                        <button class="tb-snippet" onclick="tbInsertSnippet('[Hola|Buenos dias] {nombre}')">Formal: [Hola|Buenos dias]</button>
                        <button class="tb-snippet" onclick="tbInsertSnippet('[Hola|Que tal] {nombre}, ¿como estas?')">Calido: ¿como estas?</button>
                    </div>

                    <div class="tb-snippet-cat">Intros / Presentacion</div>
                    <div class="tb-snippet-grid">
                        <button class="tb-snippet" onclick="tbInsertSnippet(', soy {bot_name} de {company_name}.')">soy [bot] de [empresa]</button>
                        <button class="tb-snippet" onclick="tbInsertSnippet(', [te escribo|te contacto|me comunico] de {company_name}.')">[te escribo|contacto] de...</button>
                        <button class="tb-snippet" onclick="tbInsertSnippet(', te [saluda|habla] {bot_name} de {company_name}.')">te [saluda|habla] [bot]...</button>
                    </div>

                    <div class="tb-snippet-cat">Cuerpo</div>
                    <div class="tb-snippet-grid">
                        <button class="tb-snippet" onclick="tbInsertSnippet('\n[Hace un tiempo|Anteriormente|Recuerdo que] [nos preguntaste|mostraste interes] por el {vehiculo}.')">Recordatorio de interes</button>
                        <button class="tb-snippet" onclick="tbInsertSnippet('\n[Queria saber|Me gustaria saber] como [te fue|te ha ido] con el {vehiculo}.')">Seguimiento de experiencia</button>
                        <button class="tb-snippet" onclick="tbInsertSnippet('\n[Vi que|Tengo entendido que] te cotizaron el {vehiculo}.')">Referencia a cotizacion</button>
                    </div>

                    <div class="tb-snippet-cat">Cierres / Preguntas</div>
                    <div class="tb-snippet-grid">
                        <button class="tb-snippet" onclick="tbInsertSnippet('\n¿[Sigues interesado|Todavia lo consideras|Ya resolviste algo]?')">Pregunta abierta</button>
                        <button class="tb-snippet" onclick="tbInsertSnippet('\n¿[Te puedo ayudar|Hay algo en lo que pueda apoyarte] con [eso|tu busqueda]?')">Oferta de ayuda</button>
                        <button class="tb-snippet" onclick="tbInsertSnippet('\n¿[Que tal te parecio|Como te fue|Que te parecio]?')">Pedir opinion</button>
                        <button class="tb-snippet" onclick="tbInsertSnippet('\n[Quedo al pendiente|Estoy para ayudarte|Con gusto te apoyo].')">Cierre suave</button>
                    </div>
                </div>

                <!-- Good vs Bad examples -->
                <div class="tb-panel" style="margin-bottom: 16px;">
                    <h3>Ejemplos: Bien vs Mal</h3>
                    <p style="font-size: 12px; color: #94a3b8; margin-bottom: 10px;">Aprende a distinguir un buen template de uno debil</p>

                    <div class="tb-compare">
                        <div class="tb-compare-card good">
                            <div class="tb-compare-label">Recomendado</div>
                            [Hola|Que tal] {nombre}, soy {bot_name} de {company_name}.<br>
                            [Recuerdo que preguntaste|Vi tu interes] por el {vehiculo}. ¿[Sigues evaluando|Ya resolviste]?
                            <div class="tb-compare-why">Corto, con contexto, con pregunta, con variacion.</div>
                        </div>
                        <div class="tb-compare-card bad">
                            <div class="tb-compare-label">Evitar</div>
                            Hola, te escribo de Go-On Zapata para informarte que tenemos una promocion especial en camiones seminuevos con precios de liquidacion, aprovecha esta oportunidad unica.
                            <div class="tb-compare-why">Largo, sin nombre, sin vehiculo, sin pregunta, palabras spam.</div>
                        </div>
                    </div>

                    <div class="tb-compare" style="margin-top: 8px;">
                        <div class="tb-compare-card good">
                            <div class="tb-compare-label">Recomendado</div>
                            [Buenas|Hola] {nombre}, ¿[como te fue|que tal te parecio] el {vehiculo} [cuando viniste a verlo|en tu visita]?
                            <div class="tb-compare-why">Directo, personalizado, pregunta natural.</div>
                        </div>
                        <div class="tb-compare-card bad">
                            <div class="tb-compare-label">Evitar</div>
                            Hola buen dia. Le escribo para darle seguimiento a su visita en nuestra agencia. Quedamos a sus ordenes para cualquier duda o aclaracion que pueda tener. Saludos cordiales.
                            <div class="tb-compare-why">Sin nombre, sin vehiculo, sin spintax, demasiado formal, sin pregunta.</div>
                        </div>
                    </div>
                </div>

                <!-- Templates de ejemplo completos -->
                <div class="tb-panel">
                    <h3>Plantillas completas</h3>
                    <p style="font-size: 12px; color: #94a3b8; margin-bottom: 12px;">Clic en una plantilla para cargarla en el editor</p>

                    <div class="tb-example" onclick="tbLoadExample(this)">
                        <div class="tb-example-label">Lead perdido</div>
                        [Hola|Buenas|Que tal] {nombre}, [te escribo|te contacto|me comunico] de {company_name}.
[Hace un tiempo nos preguntaste|Vi que en su momento preguntaste|Recuerdo que preguntaste] por el {vehiculo}. ¿[Sigues interesado|Todavia lo consideras|Ya resolviste tu compra]?
                    </div>

                    <div class="tb-example" onclick="tbLoadExample(this)" style="margin-top: 8px;">
                        <div class="tb-example-label">Seguimiento cotizacion</div>
                        [Hola|Buenas] {nombre}, soy {bot_name} de {company_name}.
¿[Te pudieron resolver|Como te fue con|Te dieron respuesta sobre] tu consulta [sobre|del|acerca del] {vehiculo}?
                    </div>

                    <div class="tb-example" onclick="tbLoadExample(this)" style="margin-top: 8px;">
                        <div class="tb-example-label">Post visita</div>
                        [Hola|Que tal] {nombre}, soy {bot_name} de {company_name}.
¿[Que tal te parecio|Como te fue con|Que impresion te llevo] el {vehiculo} [cuando viniste a verlo|en tu visita]?
                    </div>

                    <div class="tb-example" onclick="tbLoadExample(this)" style="margin-top: 8px;">
                        <div class="tb-example-label">Servicio / postventa</div>
                        [Hola|Buenas] {nombre}, [te contacto|me comunico] de {company_name}.
¿[Como te han atendido|Que tal la atencion|Como ha sido tu experiencia] con [tu unidad|el {vehiculo}]? [Queremos asegurarnos de que todo vaya bien|Nos importa tu experiencia].
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- ═══════════ TAB 3: CSV TEMPLATE GENERATOR ═══════════ -->
    <div class="tab-content" id="tab-csvgen">
        <div style="max-width: 700px; margin: 0 auto;">

            <div class="tb-panel" style="margin-bottom: 24px;">
                <h3>Generador de Templates Personalizados por CSV</h3>
                <p style="font-size:13px; color:#94a3b8; margin-top:8px; line-height:1.6;">
                    Sube tu exportación de Monday.com (CSV). La IA leerá el <strong>Resumen</strong>, <strong>Vehículo</strong> y <strong>Nombre</strong>
                    de cada prospecto y generará un template personalizado con spintax para cada uno.
                    Descargas el mismo CSV con la columna <strong>Template</strong> ya rellenada, listo para importar.
                </p>

                <div style="margin-top: 20px;">
                    <label class="tb-label">Archivo CSV (exportación de Monday)</label>
                    <input type="file" id="csvFileInput" accept=".csv" style="
                        display: block; width: 100%; padding: 12px 14px;
                        border: 2px dashed #334155; border-radius: 8px;
                        background: #0f172a; color: #e2e8f0; font-size: 14px;
                        cursor: pointer; outline: none; margin-top: 6px;
                    " onchange="csvFileSelected(this)"/>
                    <p id="csvFileHint" style="font-size:12px; color:#64748b; margin-top:6px; font-style:italic;">
                        Ningún archivo seleccionado
                    </p>
                </div>

                <div style="margin-top: 20px; padding: 14px; background: #0f172a; border: 1px solid #334155; border-radius: 8px; font-size: 12px; color: #64748b;">
                    <strong style="color:#94a3b8;">Columnas que detecta automáticamente:</strong>
                    <div style="margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px;">
                        <span style="background:#1e293b; border:1px solid #334155; padding:3px 10px; border-radius:6px; color:#94a3b8; font-family:monospace;">Elemento / Nombre</span>
                        <span style="background:#1e293b; border:1px solid #334155; padding:3px 10px; border-radius:6px; color:#94a3b8; font-family:monospace;">Vehículo / Vehiculo</span>
                        <span style="background:#1e293b; border:1px solid #334155; padding:3px 10px; border-radius:6px; color:#94a3b8; font-family:monospace;">Resumen / Notas</span>
                        <span style="background:#1e293b; border:1px solid #334155; padding:3px 10px; border-radius:6px; color:#22d3ee; font-family:monospace;">Template ← se llena</span>
                    </div>
                </div>

                <div id="csvPreviewBox" style="display:none; margin-top:20px;">
                    <div class="tb-label" style="margin-bottom:8px;">Vista previa del CSV cargado</div>
                    <div id="csvPreviewTable" style="overflow-x:auto; background:#0f172a; border:1px solid #334155; border-radius:8px; padding:12px; font-size:12px; color:#94a3b8; font-family:monospace; max-height:200px; overflow-y:auto;"></div>
                    <p id="csvRowCount" style="font-size:12px; color:#64748b; margin-top:8px;"></p>
                </div>

                <div style="margin-top: 24px; display: flex; gap: 12px; flex-wrap: wrap; align-items: center;">
                    <button class="btn btn-start" id="csvGenerateBtn" onclick="csvGenerate()" disabled style="font-size:15px; padding:12px 32px;">
                        Generar Templates con IA
                    </button>
                    <span id="csvSpinner" style="display:none; color:#94a3b8; font-size:13px;">
                        <span class="spinner"></span>&nbsp; Procesando con IA, espera un momento...
                    </span>
                </div>
            </div>

            <div id="csvResultBox" style="display:none;">
                <div class="tb-panel" style="border-color: #22c55e;">
                    <h3 style="color:#22c55e;">Templates generados</h3>
                    <p id="csvResultSummary" style="font-size:13px; color:#94a3b8; margin-top:6px;"></p>

                    <div id="csvResultPreview" style="margin-top:16px; max-height:320px; overflow-y:auto; display: flex; flex-direction: column; gap: 12px;"></div>

                    <div style="margin-top: 20px; display: flex; gap: 10px;">
                        <button class="btn btn-start" onclick="csvDownload()">Descargar CSV con Templates</button>
                        <button class="btn btn-refresh" onclick="csvReset()">Nueva carga</button>
                    </div>
                </div>
            </div>

            <div id="csvErrorBox" style="display:none;">
                <div class="tb-panel" style="border-color: #ef4444;">
                    <h3 style="color:#ef4444;">Error al generar</h3>
                    <p id="csvErrorMsg" style="font-size:13px; color:#fca5a5; margin-top:6px;"></p>
                    <button class="btn btn-refresh" onclick="csvReset()" style="margin-top:12px;">Intentar de nuevo</button>
                </div>
            </div>
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
    if (!r.ok) throw new Error('HTTP ' + r.status);
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
        addLog('Error health: ' + e.message, 'log-error');
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
        if (d.is_office_hours) {
            wb.textContent = 'Ventana abierta (' + (d.schedule_today || '') + ')';
            wb.className = 'window-badge open';
        } else {
            wb.textContent = 'Fuera de horario (' + (d.current_time_mx || '') + ' MX)';
            wb.className = 'window-badge closed';
        }
        addLog('Status cargado: ' + (d.today || '') + ' ' + (d.current_time_mx || '') + ' — ' + (d.is_office_hours ? 'Horario activo' : 'Fuera de horario'), 'log-info');
        return d;
    } catch (e) {
        addLog('Error cargando status: ' + e.message, 'log-error');
        return null;
    }
}

async function loadGroups() {
    try {
        const d = await fetchJSON(BASE + '/admin/groups');
        groups = d.groups || [];
        renderCampaigns();
    } catch (e) {
        document.getElementById('campaignGrid').innerHTML = '<div class="empty-state"><p>Error cargando campanas: ' + e.message + '</p></div>';
        addLog('Error groups: ' + e.message, 'log-error');
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

// ══════════════════════════════════════════
// TAB NAVIGATION
// ══════════════════════════════════════════
function switchTab(tabName) {
    // Update buttons
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

    // Activate selected
    const tabs = { 'campaigns': 0, 'builder': 1, 'csvgen': 2 };
    document.querySelectorAll('.tab-btn')[tabs[tabName]].classList.add('active');
    document.getElementById('tab-' + tabName).classList.add('active');
}

// ══════════════════════════════════════════
// TEMPLATE BUILDER V2
// ══════════════════════════════════════════

const TB_BOT_NAME = 'Estefania Fernandez';
const TB_COMPANY = 'Go-On Zapata';
const TB_URL = 'go-on.mx';
const TB_SPAM_WORDS = ['precio', 'oferta', 'descuento', 'promocion', 'promoción', 'gratis', 'liquidacion', 'liquidación', 'remate', 'ganga', 'oportunidad unica', 'oportunidad única', 'aprovecha', 'no te lo pierdas', 'urgente', 'ultima oportunidad', 'última oportunidad'];

// ── Core: Resolve spintax ──
function resolveSpintax(text) {
    const pattern = /\[([^\[\]]+\|[^\[\]]+)\]/;
    let result = text;
    let safety = 0;
    while (pattern.test(result) && safety < 50) {
        result = result.replace(pattern, function(match, group) {
            const options = group.split('|');
            return options[Math.floor(Math.random() * options.length)];
        });
        safety++;
    }
    return result;
}

// ── Core: Replace variables ──
function tbReplaceVars(text) {
    const nombre = document.getElementById('tbNombre').value || 'cliente';
    const vehiculo = document.getElementById('tbVehiculo').value || 'tu unidad de interes';
    const notas = document.getElementById('tbNotas').value || '';
    const resumen = document.getElementById('tbResumen').value || '';
    return text
        .replace(/\{nombre\}/g, nombre)
        .replace(/\{vehiculo\}/g, vehiculo)
        .replace(/\{bot_name\}/g, TB_BOT_NAME)
        .replace(/\{company_name\}/g, TB_COMPANY)
        .replace(/\{company_url\}/g, TB_URL)
        .replace(/\{notas\}/g, notas)
        .replace(/\{resumen\}/g, resumen)
        .replace(/\{mensaje\}/g, '');
}

// ── Core: Detect if template has bot presentation ──
function tbHasPresentation(text) {
    var lower = text.toLowerCase();
    var botFirst = TB_BOT_NAME.toLowerCase().split(' ')[0];
    if (lower.indexOf(botFirst) !== -1) return true;
    var patterns = [/\bsoy\s+\w+/, /\bte saluda\s+\w+/, /\bte escribe\s+\w+/, /\bmi nombre es\s+\w+/, /\ble habla\s+\w+/];
    for (var i = 0; i < patterns.length; i++) {
        if (patterns[i].test(lower)) return true;
    }
    return false;
}

// ── Core: Inject bot intro (mirrors Python logic) ──
function tbInjectIntro(text) {
    var greetPat = /^(hola[,.]?\s*(?:buen(?:os)?\s+(?:d[ií]as?|tardes?|noches?))?[,.]?\s*|buen(?:os)?\s+(?:d[ií]as?|tardes?|noches?)[,.]?\s*|¿?c[oó]mo\s+(?:te\s+encuentras|est[aá]s)\??[,.]?\s*)/i;
    var match = text.match(greetPat);
    if (match) {
        var greeting = match[0].replace(/[,.\s]+$/, '');
        var rest = text.substring(match[0].length).replace(/^\s+/, '');
        return greeting + ', soy ' + TB_BOT_NAME + ' de ' + TB_COMPANY + '.\n' + rest;
    }
    return 'Hola, soy ' + TB_BOT_NAME + ' de ' + TB_COMPANY + '.\n' + text;
}

// ── Validate: syntax + quality ──
function validateTemplate(text) {
    var issues = [];
    var depth = 0;
    for (var i = 0; i < text.length; i++) {
        if (text[i] === '[') depth++;
        if (text[i] === ']') depth--;
        if (depth < 0) { issues.push('Corchete ] sin su [ correspondiente (pos ' + (i+1) + ')'); break; }
    }
    if (depth > 0) issues.push('Falta cerrar ' + depth + ' corchete(s)');
    var noPipe = text.match(/\[[^\[\]|]+\]/g);
    if (noPipe) noPipe.forEach(function(m) { issues.push('Bloque ' + m + ' sin | — no es spintax'); });
    var vars = text.match(/\{(\w+)\}/g) || [];
    var known = ['nombre','vehiculo','bot_name','company_name','company_url','notas','resumen','mensaje'];
    vars.forEach(function(v) { if (known.indexOf(v.slice(1,-1)) === -1) issues.push('Variable desconocida: ' + v); });
    return issues;
}

// ── Count combinations ──
function countCombinations(text) {
    var blocks = text.match(/\[([^\[\]]+\|[^\[\]]+)\]/g) || [];
    if (!blocks.length) return 1;
    var total = 1;
    blocks.forEach(function(b) { total *= b.slice(1,-1).split('|').length; });
    return total;
}

// ── Detect spam words ──
function detectSpamWords(text) {
    var lower = text.toLowerCase();
    var found = [];
    TB_SPAM_WORDS.forEach(function(w) { if (lower.indexOf(w) !== -1) found.push(w); });
    return found;
}

// ── Quality analysis ──
function analyzeQuality(template, resolvedMsg) {
    var checks = [];
    var score = 0;
    var maxScore = 0;

    // 1. Has question
    maxScore += 2;
    if (/\?/.test(template)) { checks.push({ok: true, text: 'Termina con pregunta'}); score += 2; }
    else { checks.push({ok: false, text: 'Sin pregunta — los mensajes con pregunta generan mas respuestas'}); }

    // 2. Uses vehiculo
    maxScore += 2;
    if (/\{vehiculo\}/.test(template)) { checks.push({ok: true, text: 'Incluye vehiculo de interes'}); score += 2; }
    else { checks.push({ok: false, text: 'No menciona {vehiculo} — pierde contexto'}); }

    // 3. Message length
    maxScore += 2;
    var charCount = resolvedMsg.length;
    if (charCount <= 250) { checks.push({ok: true, text: 'Longitud adecuada (' + charCount + ' caracteres)'}); score += 2; }
    else if (charCount <= 400) { checks.push({ok: 'warn', text: 'Mensaje largo (' + charCount + ' chars) — los cortos funcionan mejor'}); score += 1; }
    else { checks.push({ok: false, text: 'Mensaje muy largo (' + charCount + ' chars) — reducir a menos de 250'}); }

    // 4. Spintax variation
    maxScore += 2;
    var combos = countCombinations(template);
    if (combos >= 9) { checks.push({ok: true, text: 'Buena variacion (' + combos + ' combinaciones)'}); score += 2; }
    else if (combos >= 3) { checks.push({ok: 'warn', text: 'Variacion baja (' + combos + ' combos) — agregar mas opciones'}); score += 1; }
    else { checks.push({ok: false, text: 'Sin variacion (' + combos + ' combo) — agregar bloques [opcion1|opcion2|opcion3]'}); }

    // 5. Spam words
    maxScore += 2;
    var spam = detectSpamWords(template);
    if (spam.length === 0) { checks.push({ok: true, text: 'Sin palabras comerciales riesgosas'}); score += 2; }
    else { checks.push({ok: false, text: 'Palabras riesgosas detectadas: ' + spam.join(', ')}); }

    // 6. Syntax valid
    maxScore += 1;
    var syntaxIssues = validateTemplate(template);
    if (syntaxIssues.length === 0) { checks.push({ok: true, text: 'Sintaxis correcta'}); score += 1; }
    else { checks.push({ok: false, text: 'Errores de sintaxis: ' + syntaxIssues[0]}); }

    // 7. Line count
    maxScore += 1;
    var lineCount = resolvedMsg.split('\n').filter(function(l) { return l.trim(); }).length;
    if (lineCount <= 3) { checks.push({ok: true, text: lineCount + ' lineas — formato adecuado'}); score += 1; }
    else { checks.push({ok: 'warn', text: lineCount + ' lineas — WhatsApp es chat, no email'}); }

    // Calculate level
    var pct = score / maxScore;
    var level = pct >= 0.8 ? 'green' : pct >= 0.5 ? 'yellow' : 'red';
    var label = pct >= 0.8 ? 'Listo para usar' : pct >= 0.5 ? 'Usable, pero mejorable' : 'Requiere ajustes';

    return { level: level, label: label, checks: checks, charCount: charCount, wordCount: resolvedMsg.split(/\s+/).filter(Boolean).length, lineCount: lineCount, combos: countCombinations(template) };
}

// ── Render WhatsApp bubble ──
function renderBubble(msg, num, injected) {
    var timeH = 9 + Math.floor(Math.random() * 8);
    var timeM = Math.floor(Math.random() * 60);
    var timeStr = (timeH < 10 ? '0' : '') + timeH + ':' + (timeM < 10 ? '0' : '') + timeM;

    var content = '';
    if (injected) {
        var parts = msg.split('\n');
        content = '<span class="tb-wa-injected">' + escHTML(parts[0]) + '</span>\n' + escHTML(parts.slice(1).join('\n'));
    } else {
        content = escHTML(msg);
    }

    return '<div class="tb-wa-bubble">'
        + '<span class="tb-wa-num">Variante ' + num + '</span>'
        + content
        + '<span class="tb-wa-time">' + timeStr + '</span>'
        + '</div>';
}

function escHTML(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

// ── Generate single resolved message ──
function tbResolveOne(template) {
    var msg = resolveSpintax(template);
    msg = tbReplaceVars(msg);
    return msg;
}

// ── Update quality UI ──
function tbUpdateQuality(template) {
    if (!template.trim()) {
        document.getElementById('tbSemaphore').style.display = 'none';
        document.getElementById('tbStatsBar').style.display = 'none';
        document.getElementById('tbInjectAlert').className = 'tb-inject-alert';
        return;
    }

    var resolved = tbResolveOne(template);
    var q = analyzeQuality(template, resolved);
    var needsInject = !tbHasPresentation(template);

    // Semaphore
    var sem = document.getElementById('tbSemaphore');
    sem.style.display = 'flex';
    sem.className = 'tb-semaphore ' + q.level;
    document.getElementById('tbSemText').textContent = q.label;

    var checksEl = document.getElementById('tbChecks');
    checksEl.innerHTML = q.checks.map(function(c) {
        var cls = c.ok === true ? 'tb-check-ok' : c.ok === 'warn' ? 'tb-check-warn' : 'tb-check-fail';
        var icon = c.ok === true ? '&#10003;' : c.ok === 'warn' ? '&#9888;' : '&#10007;';
        return '<li class="' + cls + '"><span>' + icon + '</span> ' + c.text + '</li>';
    }).join('');

    // Stats bar
    var statsBar = document.getElementById('tbStatsBar');
    statsBar.style.display = 'grid';

    var charStat = document.getElementById('tbStatChars');
    charStat.querySelector('.tb-stat-val').textContent = q.charCount;
    charStat.className = 'tb-stat' + (q.charCount > 400 ? ' danger' : q.charCount > 250 ? ' warn' : '');

    var wordStat = document.getElementById('tbStatWords');
    wordStat.querySelector('.tb-stat-val').textContent = q.wordCount;

    var lineStat = document.getElementById('tbStatLines');
    lineStat.querySelector('.tb-stat-val').textContent = q.lineCount;
    lineStat.className = 'tb-stat' + (q.lineCount > 3 ? ' warn' : '');

    var comboStat = document.getElementById('tbStatCombos');
    comboStat.querySelector('.tb-stat-val').textContent = q.combos;
    comboStat.className = 'tb-stat' + (q.combos < 3 ? ' warn' : '');

    // Inject alert
    var injectAlert = document.getElementById('tbInjectAlert');
    injectAlert.className = 'tb-inject-alert' + (needsInject ? ' visible' : '');

    // Validation box
    var validation = document.getElementById('tbValidation');
    var syntaxIssues = validateTemplate(template);
    if (syntaxIssues.length > 0) {
        validation.className = 'tb-validation warning';
        validation.innerHTML = syntaxIssues.join('<br>');
    } else {
        validation.className = 'tb-validation ok';
        validation.textContent = 'Sintaxis correcta';
    }

    // Counter
    document.getElementById('tbCounter').innerHTML = 'Combinaciones: <strong>' + q.combos.toLocaleString() + '</strong>';
}

// ── Generate preview (1 bubble) ──
function tbPreview() {
    var template = document.getElementById('tbTemplate').value.trim();
    if (!template) return;

    tbUpdateQuality(template);

    var needsInject = !tbHasPresentation(template);
    var container = document.getElementById('tbWaContainer');
    var msg = tbResolveOne(template);
    if (needsInject) msg = tbInjectIntro(msg);

    lastPreviewText = msg;
    container.innerHTML = renderBubble(msg, 1, needsInject);
}

// ── Generate 5 variants ──
function tbGenerate5() {
    var template = document.getElementById('tbTemplate').value.trim();
    if (!template) return;

    tbUpdateQuality(template);

    var needsInject = !tbHasPresentation(template);
    var container = document.getElementById('tbWaContainer');
    var html = '';
    var allMsgs = [];

    for (var i = 1; i <= 5; i++) {
        var msg = tbResolveOne(template);
        if (needsInject) msg = tbInjectIntro(msg);
        allMsgs.push(msg);
        html += renderBubble(msg, i, needsInject);
    }

    lastPreviewText = allMsgs[0];
    container.innerHTML = html;
}

// ── Insert variable at cursor ──
function tbInsertVar(varName) {
    var ta = document.getElementById('tbTemplate');
    var start = ta.selectionStart;
    var end = ta.selectionEnd;
    var text = ta.value;
    ta.value = text.substring(0, start) + varName + text.substring(end);
    ta.focus();
    ta.selectionStart = ta.selectionEnd = start + varName.length;
    tbUpdateQuality(ta.value);
}

// ── Load example ──
function tbLoadExample(el) {
    var lines = el.innerText.split('\n');
    var template = lines.slice(1).join('\n').trim();
    document.getElementById('tbTemplate').value = template;
    tbGenerate5();
}

// ── Copy template ──
function tbCopyTemplate() {
    var template = document.getElementById('tbTemplate').value.trim();
    if (!template) { toast('No hay template para copiar', 'error'); return; }
    navigator.clipboard.writeText(template).then(function() {
        toast('Template copiado al portapapeles', 'success');
    }).catch(function() {
        var ta = document.createElement('textarea');
        ta.value = template;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        toast('Template copiado al portapapeles', 'success');
    });
}

// ── Insert snippet at cursor ──
function tbInsertSnippet(snippet) {
    var ta = document.getElementById('tbTemplate');
    var start = ta.selectionStart;
    var end = ta.selectionEnd;
    var text = ta.value;
    ta.value = text.substring(0, start) + snippet + text.substring(end);
    ta.focus();
    ta.selectionStart = ta.selectionEnd = start + snippet.length;
    tbUpdateQuality(ta.value);
    toast('Bloque insertado', 'success');
}

// ── Copy resolved preview ──
var lastPreviewText = '';
function tbCopyPreview() {
    if (!lastPreviewText) {
        toast('Genera una vista previa primero', 'error');
        return;
    }
    navigator.clipboard.writeText(lastPreviewText).then(function() {
        toast('Preview copiado al portapapeles', 'success');
    }).catch(function() {
        var ta = document.createElement('textarea');
        ta.value = lastPreviewText;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        toast('Preview copiado al portapapeles', 'success');
    });
}

// ── Campaign type selector ──
var TB_CAMPAIGN_PRESETS = {
    lost_lead: {
        hint: 'Tono consultivo y empático. El lead no respondio o no avanzo. Objetivo: reactivar interes sin presionar.',
        template: '[Hola|Buenas|Que tal] {nombre}, [te escribo|te contacto|me comunico] de {company_name}.\n[Hace un tiempo nos preguntaste|Vi que en su momento preguntaste|Recuerdo que preguntaste] por el {vehiculo}. ¿[Sigues interesado|Todavia lo consideras|Ya resolviste tu compra]?'
    },
    assigned_lead: {
        hint: 'Tono de seguimiento amable. El lead fue atendido por un vendedor. Objetivo: verificar que recibio atencion y si tiene dudas.',
        template: '[Hola|Buenas] {nombre}, soy {bot_name} de {company_name}.\n¿[Te pudieron resolver|Como te fue con|Te dieron respuesta sobre] tu consulta [sobre|del|acerca del] {vehiculo}?'
    },
    attended_appointment: {
        hint: 'Tono calido post-visita. El cliente ya vino a ver el vehiculo. Objetivo: conocer su impresion y abrir puerta a siguiente paso.',
        template: '[Hola|Que tal] {nombre}, soy {bot_name} de {company_name}.\n¿[Que tal te parecio|Como te fue con|Que impresion te llevo] el {vehiculo} [cuando viniste a verlo|en tu visita]?'
    },
    customer_service: {
        hint: 'Tono de servicio. El cliente ya compro o esta en servicio. Objetivo: medir satisfaccion y detectar oportunidades de mejora.',
        template: '[Hola|Buenas] {nombre}, [te contacto|me comunico] de {company_name}.\n¿[Como te han atendido|Que tal la atencion|Como ha sido tu experiencia] con [tu unidad|el {vehiculo}]? [Queremos asegurarnos de que todo vaya bien|Nos importa tu experiencia].'
    }
};

function tbCampaignChanged() {
    var sel = document.getElementById('tbCampaignType').value;
    var hint = document.getElementById('tbCampaignHint');

    if (!sel) {
        hint.textContent = 'Selecciona un tipo para cargar sugerencias y tono recomendado';
        return;
    }

    var preset = TB_CAMPAIGN_PRESETS[sel];
    hint.textContent = preset.hint;
    document.getElementById('tbTemplate').value = preset.template;
    tbGenerate5();
}

// ── Live validation on input ──
document.getElementById('tbTemplate').addEventListener('input', function() {
    tbUpdateQuality(this.value);
});

// ══════════════════════════════════════════
// CSV TEMPLATE GENERATOR
// ══════════════════════════════════════════
let _csvFile = null;
let _csvBlob = null;
let _csvFilename = 'templates_generados.csv';

function csvFileSelected(input) {
    _csvFile = input.files[0];
    _csvBlob = null;
    document.getElementById('csvResultBox').style.display = 'none';
    document.getElementById('csvErrorBox').style.display = 'none';

    if (!_csvFile) {
        document.getElementById('csvFileHint').textContent = 'Ningún archivo seleccionado';
        document.getElementById('csvPreviewBox').style.display = 'none';
        document.getElementById('csvGenerateBtn').disabled = true;
        return;
    }

    document.getElementById('csvFileHint').textContent = _csvFile.name + ' (' + (_csvFile.size / 1024).toFixed(1) + ' KB)';
    document.getElementById('csvGenerateBtn').disabled = false;

    // Parse locally for preview
    const reader = new FileReader();
    reader.onload = function(e) {
        const text = e.target.result;
        const lines = text.split('\n').filter(l => l.trim());
        const rowCount = Math.max(0, lines.length - 1);
        document.getElementById('csvRowCount').textContent = rowCount + ' contactos detectados';

        // Simple table preview (first 5 rows)
        if (lines.length > 0) {
            const headers = lines[0].split(',').map(h => h.replace(/^"|"$/g, '').trim());
            let tableHtml = '<table style="border-collapse:collapse; width:100%;">';
            tableHtml += '<tr>' + headers.map(h => '<th style="padding:4px 8px; border-bottom:1px solid #334155; color:#22d3ee; white-space:nowrap;">' + h + '</th>').join('') + '</tr>';
            const previewRows = lines.slice(1, 4);
            previewRows.forEach(row => {
                const cells = row.split(',').map(c => c.replace(/^"|"$/g, '').trim().substring(0, 40));
                tableHtml += '<tr>' + cells.map(c => '<td style="padding:4px 8px; border-bottom:1px solid #1e293b; color:#94a3b8;">' + (c || '—') + '</td>').join('') + '</tr>';
            });
            tableHtml += '</table>';
            document.getElementById('csvPreviewTable').innerHTML = tableHtml;
        }
        document.getElementById('csvPreviewBox').style.display = 'block';
    };
    reader.readAsText(_csvFile, 'UTF-8');
}

async function csvGenerate() {
    if (!_csvFile) { toast('Selecciona un archivo CSV primero', 'error'); return; }

    document.getElementById('csvGenerateBtn').disabled = true;
    document.getElementById('csvSpinner').style.display = 'inline-flex';
    document.getElementById('csvResultBox').style.display = 'none';
    document.getElementById('csvErrorBox').style.display = 'none';

    try {
        const formData = new FormData();
        formData.append('file', _csvFile);

        const resp = await fetch(BASE + '/admin/generate-templates', {
            method: 'POST',
            body: formData,
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({error: 'Error desconocido'}));
            throw new Error(err.error || 'HTTP ' + resp.status);
        }

        // Check content-type: CSV = success, JSON = error
        const ct = resp.headers.get('content-type') || '';
        if (ct.includes('application/json')) {
            const err = await resp.json();
            throw new Error(err.error || 'Error al procesar');
        }

        // Download blob
        _csvBlob = await resp.blob();
        const cdHeader = resp.headers.get('Content-Disposition') || '';
        const fnMatch = cdHeader.match(/filename="([^"]+)"/);
        _csvFilename = fnMatch ? fnMatch[1] : 'templates_generados.csv';

        // Parse the returned CSV for preview
        const text = await _csvBlob.text();
        const lines = text.split('\n').filter(l => l.trim());
        const totalRows = Math.max(0, lines.length - 1);

        // Parse CSV line respecting quoted fields
        function parseCsvLine(line) {
            const cells = []; let cur = ''; let inQ = false;
            for (let i = 0; i < line.length; i++) {
                const ch = line[i];
                if (ch === '"') { inQ = !inQ; }
                else if (ch === ',' && !inQ) { cells.push(cur.trim()); cur = ''; }
                else { cur += ch; }
            }
            cells.push(cur.trim());
            return cells;
        }

        // Find Template column
        const headers = parseCsvLine(lines[0]).map(h => h.replace(/^"|"$/g, '').trim());
        const tmplIdx = headers.findIndex(h => h.toLowerCase() === 'template');
        const nameIdx = headers.findIndex(h => ['elemento','nombre','name'].some(k => h.toLowerCase().includes(k)));

        let previewHtml = '';
        const previewLines = lines.slice(1, 6); // show up to 5
        previewLines.forEach(line => {
            const cells = parseCsvLine(line).map(c => c.replace(/^"|"$/g, '').trim());
            const name = nameIdx >= 0 ? (cells[nameIdx] || 'Sin nombre') : 'Contacto';
            const tmpl = tmplIdx >= 0 ? (cells[tmplIdx] || '—') : '—';
            previewHtml += '<div style="background:#0f172a; border:1px solid #334155; border-radius:8px; padding:12px; margin-bottom:8px;">'
                + '<div style="font-size:12px; color:#64748b; margin-bottom:4px; text-transform:uppercase; letter-spacing:0.5px;">' + name + '</div>'
                + '<div style="font-size:13px; color:#e2e8f0; line-height:1.6; font-family:monospace;">' + tmpl + '</div>'
                + '</div>';
        });
        if (totalRows > 5) {
            previewHtml += '<div style="font-size:12px; color:#64748b; text-align:center; padding:8px;">... y ' + (totalRows - 5) + ' más</div>';
        }

        document.getElementById('csvResultSummary').textContent = totalRows + ' templates generados. Revisa la muestra y descarga el CSV.';
        document.getElementById('csvResultPreview').innerHTML = previewHtml;
        document.getElementById('csvResultBox').style.display = 'block';
        toast('Templates generados con éxito', 'success');

    } catch (e) {
        document.getElementById('csvErrorMsg').textContent = e.message;
        document.getElementById('csvErrorBox').style.display = 'block';
        toast('Error: ' + e.message, 'error');
    } finally {
        document.getElementById('csvGenerateBtn').disabled = false;
        document.getElementById('csvSpinner').style.display = 'none';
    }
}

function csvDownload() {
    if (!_csvBlob) { toast('Primero genera los templates', 'error'); return; }
    const url = URL.createObjectURL(_csvBlob);
    const a = document.createElement('a');
    a.href = url;
    a.download = _csvFilename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function csvReset() {
    _csvFile = null;
    _csvBlob = null;
    document.getElementById('csvFileInput').value = '';
    document.getElementById('csvFileHint').textContent = 'Ningún archivo seleccionado';
    document.getElementById('csvPreviewBox').style.display = 'none';
    document.getElementById('csvResultBox').style.display = 'none';
    document.getElementById('csvErrorBox').style.display = 'none';
    document.getElementById('csvGenerateBtn').disabled = true;
}

// ── Init ──
loadAll();
addLog('Dashboard conectado', 'log-success');

// Auto-refresh every 15 seconds
setInterval(loadAll, 15000);
</script>

</body>
</html>"""
