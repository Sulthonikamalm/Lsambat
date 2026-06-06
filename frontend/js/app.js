/**
 * SurabayaSambat v2 — Main App Orchestrator
 * 
 * Simplified unified flow:
 * - Toggle system ON/OFF
 * - "Ambil Data Sekarang" = discover + process queue
 * - Source management (add/delete)
 * - Usage & cost tracking with per-session transparency
 * - Pagination for comments
 * - Cache TTL to reduce server requests
 */

// ── Globals ──────────────────────────────────────────────────
const socket = io();

let commentCount = 0;
let isScraping = false;
let _commentsPage = 1;
let _commentsTotalLoaded = 0;
let _commentsTotal = 0;

// ── Init (using dashboard-init endpoint = 1 request) ─────────
document.addEventListener("DOMContentLoaded", () => {
    initDashboard();
    initChart();
});

async function initDashboard() {
    try {
        const resp = await fetch("/api/dashboard-init");
        const data = await resp.json();

        // Apply status
        applyStatusData(data.status);

        // Apply usage
        applyUsageData(data.usage);

        // Apply sources
        applySourcesData(data.sources);

        // Apply stats
        document.getElementById("statComments").textContent = data.stats.total_comments || 0;
        document.getElementById("statPosts").textContent = data.stats.total_posts || 0;
        commentCount = data.stats.total_comments || 0;
        document.getElementById("commentsBadge").textContent = commentCount;

        // Fetch comments (paginated, separate request)
        _commentsPage = 1;
        _commentsTotalLoaded = 0;
        fetchComments();

        // Fetch posts for table
        fetchPosts();

    } catch (e) {
        console.error("initDashboard error:", e);
        // Fallback: fetch individually
        fetchStatus();
        fetchUsage();
        fetchSources();
        fetchPosts();
        fetchComments();
    }
}

function loadMoreComments() {
    fetchComments(true);
}

// ── Socket.IO Event Handlers ─────────────────────────────────

socket.on("log", (entry) => {
    appendLog(entry.message, entry.level);
});

// ── Live progress state (per-run, dipakai banner di atas Riwayat Scraping)
const _liveStats = {
    running: false,
    startedAt: 0,
    total: 0,
    processed: 0,
    newComments: 0,
    baselineComments: 0,
    calls: 0,
    costPerCall: 0.032,
    timerHandle: null,
};
const USD_TO_IDR = 16000;

function _showLivePanel() {
    const el = document.getElementById("liveProgressPanel");
    if (el) el.style.display = "";
}
function _hideLivePanel() {
    const el = document.getElementById("liveProgressPanel");
    if (el) el.style.display = "none";
}
function _setLivePhase(text) {
    const el = document.getElementById("liveProgressPhase");
    if (el) el.textContent = text;
}
function _renderLiveStats() {
    const totalC = _liveStats.newComments + _liveStats.baselineComments;
    const pct = _liveStats.total > 0
        ? Math.min(100, Math.round((_liveStats.processed / _liveStats.total) * 100))
        : 0;
    const bar = document.getElementById("liveProgressBar");
    if (bar) bar.style.width = pct + "%";
    const set = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
    set("liveProcessedCount", _liveStats.processed);
    set("liveTotalCount", _liveStats.total);
    set("liveCommentsCount", totalC);
    set("liveNewCount", _liveStats.newComments);
    set("liveBaselineCount", _liveStats.baselineComments);
    set("liveCallCount", _liveStats.calls);
    const cost = Math.round(_liveStats.calls * _liveStats.costPerCall * USD_TO_IDR);
    set("liveCostCount", cost.toLocaleString("id-ID"));
}
function _startLiveTimer() {
    const elapsedEl = document.getElementById("liveProgressElapsed");
    _liveStats.timerHandle = setInterval(() => {
        if (!elapsedEl) return;
        const s = Math.floor((Date.now() - _liveStats.startedAt) / 1000);
        elapsedEl.textContent = s < 60 ? `${s}s` : `${Math.floor(s/60)}m ${s%60}s`;
    }, 1000);
}
function _stopLiveTimer() {
    if (_liveStats.timerHandle) {
        clearInterval(_liveStats.timerHandle);
        _liveStats.timerHandle = null;
    }
}
function _resetLiveStats() {
    _liveStats.running = false;
    _liveStats.startedAt = 0;
    _liveStats.total = 0;
    _liveStats.processed = 0;
    _liveStats.newComments = 0;
    _liveStats.baselineComments = 0;
    _liveStats.calls = 0;
}

socket.on("scrape_started", () => {
    isScraping = true;
    updateScrapeButtons();
    // Inisialisasi panel progress live — supaya user lihat sistem benar-benar hidup
    // (bukan menunggu sampai semua queue selesai baru ada feedback).
    _resetLiveStats();
    _liveStats.running = true;
    _liveStats.startedAt = Date.now();
    _setLivePhase("Mencari postingan baru");
    _renderLiveStats();
    _showLivePanel();
    _startLiveTimer();
    // Sync badge segera ke "🔄 Sedang Scraping…" (jangan tunggu polling 5d).
    fetchStatus();
});

socket.on("queue_ready", (data) => {
    _liveStats.total = data?.total_queued || 0;
    if (data?.cost_per_call) _liveStats.costPerCall = data.cost_per_call;
    _setLivePhase(`Mengambil komentar (${_liveStats.total} postingan)`);
    _renderLiveStats();
});

socket.on("scrape_complete", (data) => {
    isScraping = false;
    updateScrapeButtons();

    // Tutup panel live progress + stop timer
    _stopLiveTimer();
    _setLivePhase("Selesai");
    setTimeout(_hideLivePanel, 1500);  // beri jeda visual 1.5d biar user lihat hasil akhir
    _liveStats.running = false;

    // Invalidate cache
    _cache.usage.at = 0;
    _cache.sources.at = 0;
    _cache.posts.at = 0;

    _commentsPage = 1;
    _commentsTotalLoaded = 0;
    fetchPosts();
    fetchComments();
    fetchUsage();
    fetchStatus();

    if (data.success) {
        const newC = data.new_comments || 0;
        const baseC = data.baseline_comments || 0;
        const saved = data.saved_comments || (newC + baseC);
        // Tampilkan total tersimpan + breakdown supaya user tidak salah kira
        // "0 komentar" padahal sebenarnya semua tersimpan sebagai baseline.
        let msg;
        if (saved === 0) {
            msg = `Selesai: 0 komentar (tidak ada yang tersimpan).`;
        } else if (newC === 0 && baseC > 0) {
            msg = `Selesai: ${saved} komentar tersimpan (semua baseline) dari ${data.new_posts || 0} postingan baru.`;
        } else if (baseC === 0) {
            msg = `Selesai: ${newC} komentar baru dari ${data.new_posts || 0} postingan baru.`;
        } else {
            msg = `Selesai: ${saved} komentar tersimpan (${newC} baru · ${baseC} baseline) dari ${data.new_posts || 0} postingan baru.`;
        }
        showToast(msg, "success");
        // NB: titik chart sudah di-push live per item via queue_item_done,
        // jadi TIDAK perlu addChartData(saved) lagi di sini (akan double-count).
    }
});

socket.on("status_change", () => {
    fetchStatus();
});

socket.on("system_toggle", (data) => {
    fetchStatus();
});

socket.on("source_added", () => {
    fetchSources();
});

socket.on("source_deleted", () => {
    fetchSources();
});

socket.on("queue_item_done", (item) => {
    if (!_liveStats.running) return;
    _liveStats.processed += 1;
    _liveStats.calls += 1;  // 1 call comments per item (discovery dihitung terpisah)
    if (item && item.status === "completed") {
        _liveStats.newComments += (item.new_comments || 0);
        _liveStats.baselineComments += (item.baseline_comments || 0);
    }
    _renderLiveStats();
    // Push 1 titik ke grafik per item supaya grafik 'berdetak' real-time
    // (bukan satu spike di akhir scrape).
    const incr = (item && item.status === "completed")
        ? ((item.new_comments || 0) + (item.baseline_comments || 0))
        : 0;
    if (incr > 0) addChartData(incr);
});

socket.on("new_post", () => {
    // Will be refreshed on scrape_complete
});
