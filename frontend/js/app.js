/**
 * SurabayaSambat v2 — Dashboard JavaScript
 * 
 * Simplified unified flow:
 * - Toggle system ON/OFF
 * - "Ambil Data Sekarang" = discover + process queue
 * - Source management (add/delete)
 * - Usage & cost tracking with per-session transparency
 * - Pagination for comments
 * - Cache TTL to reduce server requests
 */

// ── Socket.IO Connection ─────────────────────────────────────

const socket = io();

// ── State ────────────────────────────────────────────────────

let commentCount = 0;
let isScraping = false;
let _commentsPage = 1;
let _commentsTotalLoaded = 0;
let _commentsTotal = 0;

// ── Cache TTL (30 seconds) ───────────────────────────────────

const CACHE_TTL = 30000;
let _cache = {
    usage: { data: null, at: 0 },
    sources: { data: null, at: 0 },
    posts: { data: null, at: 0 },
};

function isCacheValid(key) {
    return _cache[key].data && (Date.now() - _cache[key].at < CACHE_TTL);
}

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

        // Show/hide welcome card


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



// ── Status ───────────────────────────────────────────────────

async function fetchStatus() {
    try {
        const resp = await fetch("/api/status");
        const data = await resp.json();
        applyStatusData(data);
    } catch (e) {
        console.error("fetchStatus error:", e);
    }
}

function applyStatusData(data) {
    const toggle = document.getElementById("toggleSystem");
    const label = document.getElementById("toggleLabel");
    const desc = document.getElementById("toggleDesc");
    const badge = document.getElementById("modeBadge");
    const dot = document.getElementById("modePulseDot");
    const footerTag = document.getElementById("footerSystemTag");

    const countdownEl = document.getElementById("toggleCountdown");

    if (data.system_active) {
        toggle.checked = true;
        label.textContent = "Sistem Aktif (Demo)";
        desc.textContent = `Scraping otomatis: Setiap 2 Menit`;
        badge.textContent = "Aktif (Demo)";
        badge.className = "mode-badge mode-official_monitoring";
        dot.className = "mode-pulse-dot active";
        footerTag.textContent = "Sistem Aktif (Demo)";
        
        if (data.next_auto_scrape_time) {
            startCountdownTimer(data.next_auto_scrape_time, countdownEl);
        } else {
            stopCountdownTimer(countdownEl);
        }
    } else {
        toggle.checked = false;
        label.textContent = "Sistem Nonaktif";
        desc.textContent = "Aktifkan untuk scraping otomatis mingguan";
        badge.textContent = "Nonaktif";
        badge.className = "mode-badge mode-stopped";
        dot.className = "mode-pulse-dot";
        footerTag.textContent = "Nonaktif";
        stopCountdownTimer(countdownEl);
    }

    // Rate limit info
    if (data.tier_info) {
        const t = data.tier_info;
        document.getElementById("statScrapeCount").textContent = 
            `${t.week_count || 0}/${t.max_per_week || 4}`;
    }

    isScraping = data.is_scraping;
    updateScrapeButtons();
}

function capitalizeDay(day) {
    const map = {
        monday: "Senin", tuesday: "Selasa", wednesday: "Rabu",
        thursday: "Kamis", friday: "Jumat", saturday: "Sabtu", sunday: "Minggu"
    };
    return map[(day || "").toLowerCase()] || day;
}

let _timerInterval = null;

function startCountdownTimer(isoString, element) {
    if (!element) return;
    stopCountdownTimer(element); // clear existing
    element.style.display = "block";
    
    const targetTime = new Date(isoString).getTime();

    function update() {
        const now = new Date().getTime();
        const diff = targetTime - now;

        if (diff <= 0) {
            element.textContent = "⏳ Memulai scraping otomatis...";
            return;
        }

        const days = Math.floor(diff / (1000 * 60 * 60 * 24));
        const hours = Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
        const mins = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));

        let text = "⏳ Scraping selanjutnya dalam: ";
        if (days > 0) text += `${days} Hari `;
        if (hours > 0) text += `${hours} Jam `;
        text += `${mins} Menit`;

        element.textContent = text;
    }

    update();
    _timerInterval = setInterval(update, 60000); // update every minute
}

function stopCountdownTimer(element) {
    if (_timerInterval) {
        clearInterval(_timerInterval);
        _timerInterval = null;
    }
    if (element) element.style.display = "none";
}

function updateScrapeButtons() {
    const btnScrape = document.getElementById("btnScrape");
    const btnStop = document.getElementById("btnStopScrape");
    
    if (isScraping) {
        btnScrape.style.display = "none";
        btnStop.style.display = "inline-flex";
    } else {
        btnScrape.style.display = "inline-flex";
        btnScrape.disabled = false;
        btnStop.style.display = "none";
    }
}

// ── Usage ────────────────────────────────────────────────────

async function fetchUsage() {
    if (isCacheValid("usage")) {
        applyUsageData(_cache.usage.data);
        return;
    }
    try {
        const resp = await fetch("/api/usage");
        const data = await resp.json();
        _cache.usage = { data, at: Date.now() };
        applyUsageData(data);
    } catch (e) {
        console.error("fetchUsage error:", e);
    }
}

function applyUsageData(data) {
    document.getElementById("statApiCalls").textContent = data.total_calls || 0;
    document.getElementById("statCost").textContent = `$${(data.week_cost || 0).toFixed(2)}`;
    
    document.getElementById("usageTodayCalls").textContent = data.today_calls || 0;
    document.getElementById("usageTodayCost").textContent = `$${(data.today_cost || 0).toFixed(2)}`;
    document.getElementById("usageWeekCalls").textContent = data.week_calls || 0;
    document.getElementById("usageWeekCost").textContent = `$${(data.week_cost || 0).toFixed(2)}`;
    document.getElementById("usageTotalCalls").textContent = data.total_calls || 0;
    document.getElementById("usageTotalCost").textContent = `$${(data.total_cost || 0).toFixed(2)}`;

    // Rate limit + Quota progress bar
    if (data.rate_limit) {
        const rl = data.rate_limit;
        const weekCount = rl.week_count || 0;
        const maxWeek = rl.max_per_week || 4;
        const remaining = Math.max(0, maxWeek - weekCount);
        const pct = Math.min(100, (weekCount / maxWeek) * 100);

        document.getElementById("statScrapeCount").textContent = `${weekCount}/${maxWeek}`;
        document.getElementById("quotaProgressValue").textContent = `${weekCount} dari ${maxWeek}`;
        document.getElementById("quotaProgressFill").style.width = `${pct}%`;
        document.getElementById("quotaProgressHint").textContent = 
            remaining > 0 
                ? `Sisa kuota: ${remaining} scraping minggu ini`
                : "⚠️ Kuota minggu ini sudah habis";

        // Color warning
        const fill = document.getElementById("quotaProgressFill");
        fill.className = "quota-progress-fill" + (pct >= 100 ? " quota-full" : pct >= 75 ? " quota-warning" : "");
    }

    // Token status
    renderTokenStatus(data.token_status || []);

    // Scrape history table (Q1)
    renderScrapeHistory(data.scrape_history || []);
}

function renderTokenStatus(tokens) {
    const container = document.getElementById("tokenBadges");
    container.innerHTML = tokens.map(t => {
        const isActive = t.status === "active";
        const cls = isActive ? "token-badge-active" : "token-badge-exhausted";
        const icon = isActive ? "✅" : "❌";
        const label = isActive ? "Aktif" : "Habis";
        return `<span class="token-badge ${cls}">Token ${t.index}: ${icon} ${label}</span>`;
    }).join("");
}

function renderScrapeHistory(history) {
    const tbody = document.getElementById("scrapeHistoryBody");
    if (!history || history.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="table-empty"><p>Belum ada riwayat scraping.</p></td></tr>';
        return;
    }

    // Show newest first
    const sorted = [...history].reverse();
    tbody.innerHTML = sorted.map(h => {
        const time = formatTime(h.timestamp);
        const triggerLabel = h.trigger === "auto" ? "🤖 Otomatis" : "👤 Manual";
        const cost = h.estimated_cost_session != null ? `$${Number(h.estimated_cost_session).toFixed(3)}` : "—";
        const calls = h.api_calls_session != null ? h.api_calls_session : "—";
        return `<tr>
            <td>${time}</td>
            <td>${triggerLabel}</td>
            <td style="text-align:center;">${h.new_posts || 0}</td>
            <td style="text-align:center;">${h.new_comments || 0}</td>
            <td style="text-align:center;">${calls}</td>
            <td style="text-align:right; font-family: 'JetBrains Mono', monospace; font-weight: 700;">${cost}</td>
        </tr>`;
    }).join("");
}

// ── Sources ──────────────────────────────────────────────────

async function fetchSources() {
    try {
        const resp = await fetch("/api/sources");
        const data = await resp.json();
        applySourcesData(data);
    } catch (e) {
        console.error("fetchSources error:", e);
    }
}

function applySourcesData(data) {
    document.getElementById("sourcesBadge").textContent = data.sources?.length || 0;
    
    const container = document.getElementById("sourceCards");
    
    if (!data.sources || data.sources.length === 0) {
        container.innerHTML = '<p class="empty-text">Belum ada akun yang dipantau. Klik "Tambah Akun".</p>';
        return;
    }

    // Q13: Different labels for priority vs relevance
    container.innerHTML = data.sources.map(s => {
        const priorityLabels = { "1": "⭐ Resmi Pemkot", "2": "📋 Dinas/Pejabat", "3": "👥 Komunitas" };
        const priorityLabel = priorityLabels[s.priority_level] || s.priority_level;
        const statusBadge = s.status === "active" 
            ? '<span class="source-status-active">Aktif</span>'
            : '<span class="source-status-inactive">Nonaktif</span>';
        
        return `
            <div class="source-card">
                <div class="source-card-main">
                    <span class="source-account">${escapeHtml(s.source_account)}</span>
                    <span class="source-priority">Kategori: ${priorityLabel}</span>
                    ${statusBadge}
                </div>
                <button class="btn-icon-delete" onclick="doDeleteSource('${s.source_id}', '${escapeHtml(s.source_account)}')" title="Hapus akun">
                    <i data-lucide="trash-2"></i>
                </button>
            </div>
        `;
    }).join("");
    
    lucide.createIcons();
}

function openAddSourceModal() {
    document.getElementById("addSourceModal").setAttribute("aria-hidden", "false");
    document.getElementById("addSourceModal").classList.add("visible");
    document.getElementById("inputProfileUrl").value = "";
    document.getElementById("inputProfileUrl").focus();
}

function closeAddSourceModal() {
    document.getElementById("addSourceModal").setAttribute("aria-hidden", "true");
    document.getElementById("addSourceModal").classList.remove("visible");
}

async function doAddSource() {
    const url = document.getElementById("inputProfileUrl").value.trim();
    const priority = document.getElementById("inputPriority").value;

    if (!url) {
        showToast("Masukkan link profil Instagram.", "warning");
        return;
    }

    try {
        const resp = await fetch("/api/sources", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ profile_url: url, priority_level: priority }),
        });
        const data = await resp.json();

        if (data.success) {
            showToast(`Akun @${data.username} ditambahkan!`, "success");
            closeAddSourceModal();
            fetchSources();
        } else {
            showToast(data.error || "Gagal menambahkan akun.", "error");
        }
    } catch (e) {
        showToast("Koneksi error.", "error");
    }
}

async function doDeleteSource(sourceId, accountName) {
    showModal(
        "Hapus Akun",
        `Apakah Anda yakin ingin menghapus akun ${accountName} dari daftar pantauan?`,
        async () => {
            try {
                const resp = await fetch(`/api/sources/${sourceId}`, { method: "DELETE" });
                const data = await resp.json();
                if (data.success) {
                    showToast(`${accountName} dihapus.`, "info");
                    fetchSources();
                } else {
                    showToast(data.error || "Gagal menghapus.", "error");
                }
            } catch (e) {
                showToast("Koneksi error.", "error");
            }
        }
    );
}

// ── Posts ─────────────────────────────────────────────────────

async function fetchPosts() {
    try {
        const resp = await fetch("/api/posts");
        const posts = await resp.json();
        
        document.getElementById("postsBadge").textContent = posts.length;
        document.getElementById("statPosts").textContent = posts.length;
        
        const tbody = document.getElementById("postsBody");
        if (!posts.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="table-empty"><p>Belum ada postingan.</p></td></tr>';
            return;
        }

        tbody.innerHTML = posts.map(p => {
            const caption = (p.caption_raw || "").substring(0, 120);
            const relevance = p.post_relevance || "unknown";
            const score = p.relevance_score || "";
            let reasons = "";
            try {
                const parsed = JSON.parse(p.relevance_reasons || "[]");
                reasons = parsed.join(" | ");
            } catch(e) { reasons = p.relevance_reasons || ""; }

            const relClass = {
                high: "rel-high", medium: "rel-medium", low: "rel-low", unknown: "rel-unknown"
            }[relevance] || "rel-unknown";

            const relLabel = {
                high: "🔴 Tinggi", medium: "🟡 Sedang", low: "⚪ Rendah", unknown: "❓"
            }[relevance] || relevance;

            const url = p.post_url || "";

            return `<tr>
                <td>${escapeHtml(p.source_account || "")}</td>
                <td title="${escapeHtml(caption)}">${escapeHtml(caption) || '<em>tanpa caption</em>'}</td>
                <td style="text-align:center;">
                    <span class="rel-badge ${relClass}" title="Skor: ${score}/100 — ${reasons}">${relLabel}</span>
                    ${score ? `<br><small class="rel-score">${score}/100</small>` : ""}
                </td>
                <td style="text-align:center;">${p.comment_count_last_seen || 0}</td>
                <td><a href="${escapeHtml(url)}" target="_blank" class="link-external">Lihat postingan ↗</a></td>
            </tr>`;
        }).join("");

    } catch (e) {
        console.error("fetchPosts error:", e);
    }
}

// ── Comments (with pagination) ───────────────────────────────

async function fetchComments(append = false) {
    try {
        const page = append ? _commentsPage + 1 : 1;
        const resp = await fetch(`/api/comments?page=${page}&per_page=50`);
        const result = await resp.json();
        
        const comments = result.comments || result; // backward compat
        _commentsTotal = result.total || comments.length;
        _commentsPage = result.page || page;

        if (!append) {
            commentCount = _commentsTotal;
            _commentsTotalLoaded = 0;
        }

        document.getElementById("commentsBadge").textContent = _commentsTotal;
        document.getElementById("statComments").textContent = _commentsTotal;
        
        const tbody = document.getElementById("commentsBody");
        
        if (!append && comments.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" class="table-empty">
                <div class="table-empty-graphic">
                    <i data-lucide="inbox" class="empty-icon"></i>
                    <p class="empty-title">Belum Ada Komentar</p>
                    <p class="empty-subtitle">Komentar akan muncul setelah proses scraping.</p>
                </div>
            </td></tr>`;
            lucide.createIcons();
            updateLoadMoreButton();
            updateWelcomeCard();
            return;
        }

        const startIdx = _commentsTotalLoaded;
        const rows = comments.map((c, i) => {
            const text = (c.comment_text || "").substring(0, 200);
            const time = formatTime(c.scraped_at || c.comment_created_at || "");
            const url = c.post_url || "";
            
            return `<tr>
                <td>${startIdx + i + 1}</td>
                <td>${escapeHtml(c.source_account || "")}</td>
                <td>${escapeHtml(text)}</td>
                <td>${time}</td>
                <td><a href="${escapeHtml(url)}" target="_blank" class="link-external">Lihat postingan ↗</a></td>
            </tr>`;
        }).join("");

        if (append) {
            tbody.insertAdjacentHTML("beforeend", rows);
        } else {
            tbody.innerHTML = rows;
        }

        _commentsTotalLoaded += comments.length;
        updateLoadMoreButton();
        updateWelcomeCard();

    } catch (e) {
        console.error("fetchComments error:", e);
    }
}

function loadMoreComments() {
    fetchComments(true);
}

function updateLoadMoreButton() {
    const wrap = document.getElementById("loadMoreWrap");
    const info = document.getElementById("loadMoreInfo");
    if (_commentsTotalLoaded < _commentsTotal) {
        wrap.style.display = "flex";
        info.textContent = `Menampilkan ${_commentsTotalLoaded} dari ${_commentsTotal} komentar`;
    } else {
        wrap.style.display = _commentsTotal > 0 ? "flex" : "none";
        if (_commentsTotal > 0) {
            info.textContent = `Semua ${_commentsTotal} komentar ditampilkan`;
            document.getElementById("btnLoadMore").style.display = "none";
        }
    }
}

// ── Actions ──────────────────────────────────────────────────

async function doToggleSystem() {
    try {
        const resp = await fetch("/api/toggle-system", { method: "POST" });
        const data = await resp.json();
        fetchStatus();
    } catch (e) {
        showToast("Koneksi error.", "error");
    }
}

async function doScrapeNow() {
    // Q6: Disable button immediately to prevent double-click
    const btn = document.getElementById("btnScrape");
    btn.disabled = true;
    btn.classList.add("loading");

    try {
        const resp = await fetch("/api/scrape", { method: "POST" });
        const data = await resp.json();

        btn.classList.remove("loading");

        if (resp.status === 429) {
            btn.disabled = false;
            showToast(data.error || "Batas scraping tercapai.", "warning");
            return;
        }
        if (resp.status === 409) {
            btn.disabled = false;
            showToast(data.error || "Proses masih berjalan.", "warning");
            return;
        }
        if (!data.success) {
            btn.disabled = false;
            showToast(data.error || "Gagal memulai scraping.", "error");
            return;
        }

        isScraping = true;
        updateScrapeButtons();
        showToast("Scraping dimulai...", "info");
    } catch (e) {
        btn.disabled = false;
        btn.classList.remove("loading");
        showToast("Koneksi error.", "error");
    }
}

async function doStopScrape() {
    // Q6: Disable stop button to prevent multiple clicks
    const btn = document.getElementById("btnStopScrape");
    btn.disabled = true;
    try {
        await fetch("/api/stop-scrape", { method: "POST" });
        showToast("Menghentikan proses...", "warning");
    } catch (e) {
        btn.disabled = false;
        showToast("Koneksi error.", "error");
    }
}

function doDownload() {
    window.open("/api/download", "_blank");
}

function doReset() {
    showModal(
        "⚠️ Reset Semua Data",
        "PERINGATAN: Semua data komentar, postingan, dan antrean yang sudah dikumpulkan selama berminggu-minggu akan DIHAPUS PERMANEN. Aksi ini TIDAK BISA dibatalkan. Apakah Anda benar-benar yakin?",
        async () => {
            try {
                await fetch("/api/reset", { method: "POST" });
                showToast("Semua data direset.", "info");
                _commentsPage = 1;
                _commentsTotalLoaded = 0;
                _cache = { usage: { data: null, at: 0 }, sources: { data: null, at: 0 }, posts: { data: null, at: 0 } };
                fetchPosts();
                fetchComments();
                fetchUsage();
                fetchStatus();
                updateWelcomeCard();
            } catch (e) {
                showToast("Gagal reset.", "error");
            }
        }
    );
}

// ── Socket.IO Event Handlers ─────────────────────────────────

socket.on("log", (entry) => {
    appendLog(entry.message, entry.level);
});

socket.on("scrape_started", () => {
    isScraping = true;
    updateScrapeButtons();
});

socket.on("scrape_complete", (data) => {
    isScraping = false;
    updateScrapeButtons();

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
        const msg = `Selesai: ${data.new_comments || 0} komentar baru dari ${data.new_posts || 0} postingan baru.`;
        showToast(msg, "success");
        addChartData(data.new_comments || 0);
        updateWelcomeCard();
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

socket.on("queue_item_done", () => {
    // Refresh counts during scraping
});

socket.on("new_post", () => {
    // Will be refreshed on scrape_complete
});

// ── Log ──────────────────────────────────────────────────────

function appendLog(message, level = "info") {
    const container = document.getElementById("logContainer");
    
    // Clear empty state
    const empty = container.querySelector(".log-empty");
    if (empty) empty.remove();
    
    const entry = document.createElement("div");
    entry.className = `log-entry log-${level}`;
    
    const now = new Date();
    const timeStr = now.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    
    entry.innerHTML = `
        <span class="log-time">${timeStr}</span>
        <span class="log-msg">${escapeHtml(message)}</span>
    `;
    
    container.prepend(entry);
    
    // Keep max 100 entries
    while (container.children.length > 100) {
        container.removeChild(container.lastChild);
    }
}

// ── Chart ────────────────────────────────────────────────────

let chartInstance = null;
let chartData = { labels: [], data: [] };

function initChart() {
    const ctx = document.getElementById("cycleChart")?.getContext("2d");
    if (!ctx) return;

    chartInstance = new Chart(ctx, {
        type: "bar",
        data: {
            labels: [],
            datasets: [{
                label: "Komentar Baru",
                data: [],
                backgroundColor: "rgba(190, 18, 60, 0.7)",
                borderColor: "rgba(190, 18, 60, 1)",
                borderWidth: 1,
                borderRadius: 4,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { font: { size: 10 } },
                },
                y: {
                    beginAtZero: true,
                    grid: { color: "rgba(0,0,0,0.04)" },
                    ticks: { font: { size: 10 } },
                },
            },
        },
    });
}

function addChartData(value) {
    if (!chartInstance) return;
    const now = new Date();
    const label = now.toLocaleDateString("id-ID", { day: "2-digit", month: "short" });
    chartInstance.data.labels.push(label);
    chartInstance.data.datasets[0].data.push(value);
    
    // Keep last 15
    if (chartInstance.data.labels.length > 15) {
        chartInstance.data.labels.shift();
        chartInstance.data.datasets[0].data.shift();
    }
    chartInstance.update();
}

// ── Toast ────────────────────────────────────────────────────

function showToast(message, type = "info") {
    const container = document.getElementById("toastContainer");
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.classList.add("toast-fade-out");
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ── Modal ────────────────────────────────────────────────────

let _modalCallback = null;

function showModal(title, desc, onConfirm) {
    document.getElementById("modalTitle").textContent = title;
    document.getElementById("modalDesc").textContent = desc;
    document.getElementById("customModal").setAttribute("aria-hidden", "false");
    document.getElementById("customModal").classList.add("visible");
    _modalCallback = onConfirm;
}

document.getElementById("modalConfirm")?.addEventListener("click", () => {
    document.getElementById("customModal").setAttribute("aria-hidden", "true");
    document.getElementById("customModal").classList.remove("visible");
    if (_modalCallback) _modalCallback();
    _modalCallback = null;
});

document.getElementById("modalCancel")?.addEventListener("click", () => {
    document.getElementById("customModal").setAttribute("aria-hidden", "true");
    document.getElementById("customModal").classList.remove("visible");
    _modalCallback = null;
});

// ── Utilities ────────────────────────────────────────────────

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text || "";
    return div.innerHTML;
}

function formatTime(isoStr) {
    if (!isoStr) return "—";
    try {
        const d = new Date(isoStr);
        return d.toLocaleDateString("id-ID", {
            day: "2-digit", month: "short", year: "numeric",
            hour: "2-digit", minute: "2-digit",
        });
    } catch {
        return isoStr.substring(0, 16);
    }
}
