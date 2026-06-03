/**
 * api.js — API interactions and actions
 */

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

// ── Data Fetching ────────────────────────────────────────────

async function fetchStatus() {
    try {
        const resp = await fetch("/api/status");
        const data = await resp.json();
        applyStatusData(data);
    } catch (e) {
        console.error("fetchStatus error:", e);
    }
}

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

async function fetchSources() {
    try {
        const resp = await fetch("/api/sources");
        const data = await resp.json();
        applySourcesData(data);
    } catch (e) {
        console.error("fetchSources error:", e);
    }
}

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
            if (window.lucide) window.lucide.createIcons();
            updateLoadMoreButton();
            if (window.updateWelcomeCard) updateWelcomeCard();
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
        if (window.updateWelcomeCard) updateWelcomeCard();

    } catch (e) {
        console.error("fetchComments error:", e);
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
                if (window.updateWelcomeCard) updateWelcomeCard();
            } catch (e) {
                showToast("Gagal reset.", "error");
            }
        }
    );
}

// ── Source Management ────────────────────────────────────────

function openAddSourceModal() {
    document.getElementById("addSourceModal").setAttribute("aria-hidden", "false");
    document.getElementById("addSourceModal").classList.add("visible");
    const inputField = document.getElementById("inputProfileUrl");
    inputField.value = "";
    document.getElementById("platformHint").innerHTML = "";
    inputField.focus();
}

function closeAddSourceModal() {
    document.getElementById("addSourceModal").setAttribute("aria-hidden", "true");
    document.getElementById("addSourceModal").classList.remove("visible");
}

async function doAddSource() {
    const url = document.getElementById("inputProfileUrl").value.trim();
    const priority = document.getElementById("inputPriority").value;

    if (!url) {
        showToast("Masukkan link profil Instagram atau Facebook.", "warning");
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
// ── Platform Auto-Detect Listener ────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    const inputUrl = document.getElementById("inputProfileUrl");
    const hint = document.getElementById("platformHint");
    
    if (inputUrl && hint) {
        inputUrl.addEventListener("input", (e) => {
            const val = e.target.value.toLowerCase().trim();
            if (!val) {
                hint.innerHTML = "";
                return;
            }
            if (val.includes("facebook.com") || val.includes("fb.com")) {
                hint.innerHTML = "Platform: <span style='color: #1877F2;'>📘 Facebook</span>";
            } else if (val.includes("instagram.com") || val.startsWith("@") || val.length > 2) {
                hint.innerHTML = "Platform: <span style='color: #E1306C;'>📷 Instagram</span>";
            } else {
                hint.innerHTML = "";
            }
        });
    }
});
