/**
 * ui.js — UI rendering and DOM manipulation
 */

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

function applyUsageData(data) {
    document.getElementById("statApiCalls").textContent = data.total_calls || 0;
    const USD_TO_IDR = 16000;
    document.getElementById("statCost").textContent = `Rp ${Math.round((data.week_cost || 0) * USD_TO_IDR).toLocaleString("id-ID")}`;
    
    document.getElementById("usageTodayCalls").textContent = data.today_calls || 0;
    document.getElementById("usageTodayCost").textContent = `Rp ${Math.round((data.today_cost || 0) * USD_TO_IDR).toLocaleString("id-ID")}`;
    document.getElementById("usageWeekCalls").textContent = data.week_calls || 0;
    document.getElementById("usageWeekCost").textContent = `Rp ${Math.round((data.week_cost || 0) * USD_TO_IDR).toLocaleString("id-ID")}`;
    document.getElementById("usageTotalCalls").textContent = data.total_calls || 0;
    document.getElementById("usageTotalCost").textContent = `Rp ${Math.round((data.total_cost || 0) * USD_TO_IDR).toLocaleString("id-ID")}`;

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
                ? `Sisa kuota: ${remaining} pengambilan data minggu ini`
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
        const label = isActive ? "Tersambung" : "Terputus";
        return `<span class="token-badge ${cls}">Koneksi ${t.index}: ${icon} ${label}</span>`;
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
        const USD_TO_IDR = 16000;
        const cost = h.estimated_cost_session != null ? `Rp ${Math.round(Number(h.estimated_cost_session) * USD_TO_IDR).toLocaleString("id-ID")}` : "—";
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

function applySourcesData(data) {
    const total = data.sources?.length || 0;
    document.getElementById("sourcesBadge").textContent = total;

    const igContainer = document.getElementById("sourceCardsIG");
    const fbContainer = document.getElementById("sourceCardsFB");

    if (!data.sources || data.sources.length === 0) {
        igContainer.innerHTML = '<p class="empty-text">Belum ada akun Instagram.</p>';
        fbContainer.innerHTML = '<p class="empty-text">Belum ada akun Facebook.</p>';
        document.getElementById("igCountBadge").textContent = "0";
        document.getElementById("fbCountBadge").textContent = "0";
        return;
    }

    // Pisahkan berdasarkan platform
    const igSources = data.sources.filter(s =>
        (s.platform || "instagram").toLowerCase() === "instagram"
    );
    const fbSources = data.sources.filter(s =>
        (s.platform || "").toLowerCase() === "facebook"
    );

    document.getElementById("igCountBadge").textContent = igSources.length;
    document.getElementById("fbCountBadge").textContent = fbSources.length;

    // Q13: Different labels for priority vs relevance
    const priorityLabels = { "1": "⭐ Resmi Pemkot", "2": "📋 Dinas/Pejabat", "3": "👥 Komunitas" };

    function renderCards(sources, emptyMsg) {
        if (sources.length === 0) {
            return `<p class="empty-text">${emptyMsg}</p>`;
        }
        return sources.map(s => {
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
    }

    igContainer.innerHTML = renderCards(igSources, 'Belum ada akun Instagram. Klik "Tambah Akun".');
    fbContainer.innerHTML = renderCards(fbSources, 'Belum ada akun Facebook. Klik "Tambah Akun".');

    if (window.lucide) window.lucide.createIcons();
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
