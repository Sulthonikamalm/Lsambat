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
