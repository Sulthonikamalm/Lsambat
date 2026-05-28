/**
 * app.js — SurabayaSambat v2 Live Comment Monitor
 * Production-Grade Interactive Dashboard Client
 *
 * Hybrid approach: Collect All → Monitor New
 */

const socket = io();

let currentMode = "idle";
let countdownInterval = null;
let countdownSeconds = 0;
let cycleChartData = [];
let commentRowCount = 0;

// ── Toast Notification System ────────────────────────────────
function showToast(message, type = "info", duration = 4000) {
    const container = document.getElementById("toastContainer");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}-type`;

    let iconName = "info";
    if (type === "success") iconName = "check-circle";
    else if (type === "warning") iconName = "alert-triangle";
    else if (type === "error") iconName = "alert-octagon";

    toast.innerHTML = `
        <div class="toast-icon">
            <i data-lucide="${iconName}"></i>
        </div>
        <div class="toast-info">
            <div class="toast-message">${escapeHtml(message)}</div>
        </div>
    `;

    container.appendChild(toast);
    lucide.createIcons({ attrs: { class: "lucide-icon" } });

    setTimeout(() => { toast.classList.add("show"); }, 50);
    setTimeout(() => {
        toast.classList.remove("show");
        setTimeout(() => { toast.remove(); }, 300);
    }, duration);
}

// ── Custom Modal Confirmation Dialog ─────────────────────────
function showConfirmModal(title, description, confirmText = "Ya, Reset") {
    return new Promise((resolve) => {
        const modal = document.getElementById("customModal");
        const titleEl = document.getElementById("modalTitle");
        const descEl = document.getElementById("modalDesc");
        const confirmBtn = document.getElementById("modalConfirm");
        const cancelBtn = document.getElementById("modalCancel");

        if (!modal || !confirmBtn || !cancelBtn) {
            resolve(confirm(description));
            return;
        }

        titleEl.textContent = title;
        descEl.innerHTML = description;
        confirmBtn.textContent = confirmText;

        modal.classList.add("active");
        modal.setAttribute("aria-hidden", "false");

        const cleanUp = (result) => {
            modal.classList.remove("active");
            modal.setAttribute("aria-hidden", "true");
            confirmBtn.onclick = null;
            cancelBtn.onclick = null;
            resolve(result);
        };

        confirmBtn.onclick = () => cleanUp(true);
        cancelBtn.onclick = () => cleanUp(false);

        const handleKeyDown = (e) => {
            if (e.key === "Escape") {
                document.removeEventListener("keydown", handleKeyDown);
                cleanUp(false);
            }
        };
        document.addEventListener("keydown", handleKeyDown);
    });
}

// ── Chart.js Setup (Elegant Light Theme) ─────────────────────
const canvas = document.getElementById("cycleChart");
const ctx = canvas.getContext("2d");

const chartGradient = ctx.createLinearGradient(0, 0, 0, 200);
chartGradient.addColorStop(0, "rgba(190, 18, 60, 0.85)");
chartGradient.addColorStop(1, "rgba(190, 18, 60, 0.1)");

const cycleChart = new Chart(ctx, {
    type: "bar",
    data: {
        labels: [],
        datasets: [{
            label: "Komentar Baru",
            data: [],
            backgroundColor: chartGradient,
            borderColor: "#be123c",
            borderWidth: 1.5,
            borderRadius: 5,
            hoverBackgroundColor: "#9f1239",
            barPercentage: 0.6,
        }]
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
            y: {
                beginAtZero: true,
                ticks: {
                    stepSize: 1,
                    color: "#64748b",
                    font: { family: "'Plus Jakarta Sans', sans-serif", size: 10, weight: 600 }
                },
                grid: { color: "#e2e8f0" },
                border: { dash: [4, 4] }
            },
            x: {
                ticks: {
                    color: "#64748b",
                    font: { family: "'Plus Jakarta Sans', sans-serif", size: 10, weight: 600 }
                },
                grid: { display: false },
            }
        },
        plugins: {
            legend: { display: false },
            tooltip: {
                backgroundColor: "#0f172a",
                titleColor: "#f8fafc",
                bodyColor: "#cbd5e1",
                titleFont: { family: "'Plus Jakarta Sans', sans-serif", size: 11, weight: 700 },
                bodyFont: { family: "'Plus Jakarta Sans', sans-serif", size: 11 },
                padding: 10,
                cornerRadius: 6,
                borderColor: "rgba(0, 0, 0, 0.05)",
                borderWidth: 1,
                displayColors: false,
            }
        }
    }
});

// ── Initial Fetching ──────────────────────────────────────────
fetchStatus();
fetchExistingComments();

async function fetchStatus() {
    try {
        const resp = await fetch("/api/status");
        const data = await resp.json();
        updateDashboard(data);
    } catch (e) {
        showToast("Gagal memuat status sistem dari server.", "error");
    }
}

async function fetchExistingComments() {
    try {
        const resp = await fetch("/api/new-comments");
        const comments = await resp.json();
        if (comments.length > 0) {
            clearTableEmpty();
            comments.forEach(c => addTableRow(c, false));
        }
    } catch (e) {
        showToast("Gagal memuat data komentar terdeteksi.", "error");
    }
}

// ── Update Dashboard Components ──────────────────────────────
function updateDashboard(data) {
    const previousMode = currentMode;
    currentMode = data.mode || "idle";

    // Mode badge
    const badge = document.getElementById("modeBadge");
    badge.className = "mode-badge mode-" + currentMode;
    const labels = {
        idle: "Idle",
        collecting: "Mengumpulkan...",
        collected: "Data Terkumpul",
        monitoring: "Monitoring Aktif",
        stopped: "Stopped",
    };
    badge.textContent = labels[currentMode] || currentMode;

    // Glowing status pulsing dot
    const pulseDot = document.getElementById("modePulseDot");
    if (pulseDot) {
        pulseDot.className = "mode-pulse-dot";
        if (currentMode === "monitoring") {
            pulseDot.classList.add("active");
        } else if (currentMode === "collecting" || currentMode === "collected") {
            pulseDot.classList.add("warmup");
        }
    }

    // Trigger toast notification when mode changes
    if (previousMode !== currentMode && previousMode !== "idle") {
        let toastType = "info";
        if (currentMode === "monitoring") toastType = "success";
        else if (currentMode === "stopped") toastType = "warning";
        showToast(`Mode sistem berubah menjadi: ${labels[currentMode]}`, toastType);
    }

    // Header metrics counter animation
    animateCounter("statBaseline", parseInt(data.total_collected || 0));
    animateCounter("statNew", parseInt(data.total_new_comments || 0));
    animateCounter("statDup", parseInt(data.total_duplicates_prevented || 0));
    animateCounter("statCycle", parseInt(data.cycle_number || 0));
    animateCounter("statApi", parseInt(data.total_api_calls || 0));

    // Table badge
    document.getElementById("tableBadge").textContent = data.total_new_comments || 0;

    // Footer interval
    document.getElementById("footerInterval").textContent = data.interval_seconds || 40;

    // Step indicators
    updateSteps(currentMode);

    // Buttons
    updateButtons(currentMode);

    // Status message
    updateStatusMsg(currentMode, data);

    // Countdown visibility
    const cw = document.getElementById("countdownWrap");
    cw.style.display = (currentMode === "monitoring") ? "flex" : "none";
    if (currentMode !== "monitoring") stopCountdown();
}

// Quick counter animation helper
function animateCounter(id, targetValue) {
    const el = document.getElementById(id);
    if (!el) return;
    const currentValue = parseInt(el.textContent) || 0;
    if (currentValue === targetValue) {
        el.textContent = targetValue;
        return;
    }

    let start = currentValue;
    const diff = targetValue - start;
    if (diff === 0) return;

    const duration = 800;
    const startTime = performance.now();

    function update(now) {
        const progress = Math.min((now - startTime) / duration, 1);
        const value = Math.floor(start + diff * progress);
        el.textContent = value;
        if (progress < 1) {
            requestAnimationFrame(update);
        } else {
            el.textContent = targetValue;
        }
    }
    requestAnimationFrame(update);
}

function updateSteps(mode) {
    const s1 = document.getElementById("step1");
    const s2 = document.getElementById("step2");

    s1.className = "step-item";
    s2.className = "step-item";

    switch (mode) {
        case "idle":
            s1.className = "step-item active";
            break;
        case "collecting":
            s1.className = "step-item active";
            break;
        case "collected":
            s1.className = "step-item done";
            s2.className = "step-item active";
            break;
        case "monitoring":
            s1.className = "step-item done";
            s2.className = "step-item active";
            break;
        case "stopped":
            s1.className = "step-item done";
            s2.className = "step-item done";
            break;
    }
}

function updateButtons(mode) {
    const collect = document.getElementById("btnCollect");
    const start = document.getElementById("btnStart");
    const run = document.getElementById("btnRun");
    const stop = document.getElementById("btnStop");
    const dl = document.getElementById("btnDownload");

    [collect, start, run, stop, dl].forEach(b => {
        if (b) b.disabled = true;
    });

    switch (mode) {
        case "idle":
            if (collect) collect.disabled = false;
            break;
        case "collected":
            if (start) start.disabled = false;
            if (dl) dl.disabled = false;
            break;
        case "monitoring":
            if (run) run.disabled = false;
            if (stop) stop.disabled = false;
            if (dl) dl.disabled = false;
            break;
        case "stopped":
            if (dl) dl.disabled = false;
            break;
    }
}

function updateStatusMsg(mode, data) {
    const el = document.getElementById("statusMsg");
    if (!el) return;

    el.className = "status-msg";

    switch (mode) {
        case "idle":
            el.textContent = 'Klik "Kumpulkan Data Awal" untuk mengambil semua komentar yang ada di postingan.';
            break;
        case "collecting":
            el.className = "status-msg warmup";
            el.textContent = "Sedang mengumpulkan komentar dari Instagram...";
            break;
        case "collected":
            el.className = "status-msg warmup";
            const count = data.total_collected || 0;
            el.textContent =
                `${count} komentar berhasil dikumpulkan dan masuk dataset. ` +
                'Klik "Mulai Monitoring" untuk mendeteksi komentar baru secara otomatis.';
            break;
        case "monitoring":
            el.className = "status-msg active";
            el.textContent =
                "Monitoring aktif — Komentar baru yang terdeteksi akan otomatis masuk ke dataset. " +
                'Klik "Jalankan Sekarang" untuk cek manual tanpa menunggu timer.';
            break;
        case "stopped":
            el.textContent = "Monitoring dihentikan. Dataset tersedia untuk download.";
            break;
        default:
            el.textContent = "Memproses...";
    }
}

// ── Button Actions ───────────────────────────────────────────

async function doCollect() {
    const btn = document.getElementById("btnCollect");
    btn.classList.add("loading"); btn.disabled = true;
    showToast("Mengumpulkan semua komentar dari postingan...", "info");
    try {
        await fetch("/api/collect", { method: "POST" });
    }
    catch (e) {
        addLogEntry("Koneksi gagal: " + e.message, "error");
        showToast("Koneksi gagal: " + e.message, "error");
    }
    btn.classList.remove("loading");
}

async function doStartMonitoring() {
    showToast("Mengaktifkan live monitoring...", "success");
    try {
        await fetch("/api/start", { method: "POST" });
    }
    catch (e) {
        addLogEntry("Koneksi gagal: " + e.message, "error");
        showToast("Koneksi gagal: " + e.message, "error");
    }
}

async function doRunOnce() {
    const btn = document.getElementById("btnRun");
    btn.classList.add("loading"); btn.disabled = true;
    showToast("Memeriksa komentar baru di Instagram...", "info");
    try {
        await fetch("/api/run-once", { method: "POST" });
    }
    catch (e) {
        addLogEntry("Koneksi gagal: " + e.message, "error");
        showToast("Koneksi gagal: " + e.message, "error");
    }
    btn.classList.remove("loading");
}

async function doStop() {
    showToast("Menghentikan live monitoring...", "warning");
    try {
        await fetch("/api/stop", { method: "POST" });
    }
    catch (e) {
        addLogEntry("Koneksi gagal: " + e.message, "error");
        showToast("Koneksi gagal: " + e.message, "error");
    }
    stopCountdown();
}

function doDownload() {
    showToast("Mengunduh dataset komentar (CSV)...", "success");
    window.open("/api/download", "_blank");
}

async function doReset() {
    // Cek apakah ada data yang belum di-download
    const totalEl = document.getElementById("statNew");
    const totalComments = parseInt(totalEl?.textContent || "0");

    let desc = "Tindakan ini akan menghapus seluruh data yang tersimpan secara permanen. Apakah Anda yakin?";

    if (totalComments > 0) {
        desc = `Saat ini terdapat <strong>${totalComments} komentar</strong> di dataset. ` +
               `Pastikan Anda sudah <strong>mengunduh dataset (CSV)</strong> sebelum reset, ` +
               `karena semua data akan dihapus permanen dan proses pengumpulan ulang membutuhkan API call baru.`;
    }

    const approved = await showConfirmModal(
        "Reset Semua Data?",
        desc,
        "Ya, Reset"
    );
    if (!approved) return;

    showToast("Mereset seluruh data sistem...", "warning");
    try {
        await fetch("/api/reset", { method: "POST" });

        // Clean activity log
        document.getElementById("logContainer").innerHTML = `
            <div class="log-empty">
                <i data-lucide="terminal" class="empty-icon"></i>
                <p>Belum ada aktivitas monitoring tercatat.</p>
            </div>
        `;

        // Clean table rows
        document.getElementById("dataBody").innerHTML = `
            <tr>
                <td colspan="6" class="table-empty">
                    <div class="table-empty-graphic">
                        <i data-lucide="inbox" class="empty-icon"></i>
                        <p class="empty-title">Belum Ada Komentar</p>
                        <p class="empty-subtitle">Data komentar keluhan warga akan muncul di sini setelah sistem mengumpulkan data dan mendeteksi komentar.</p>
                    </div>
                </td>
            </tr>
        `;

        commentRowCount = 0;
        cycleChartData = [];
        updateChartDisplay();

        lucide.createIcons();
    } catch (e) {
        addLogEntry("Koneksi gagal: " + e.message, "error");
        showToast("Koneksi gagal: " + e.message, "error");
    }
    stopCountdown();
}

// ── Log Entry Handling ────────────────────────────────────────

function addLogEntry(message, level) {
    const container = document.getElementById("logContainer");
    if (!container) return;

    const empty = container.querySelector(".log-empty");
    if (empty) empty.remove();

    const entry = document.createElement("div");
    entry.className = "log-entry log-" + (level || "info");

    const now = new Date();
    const t = now.toLocaleTimeString("id-ID", { hour12: false });

    entry.innerHTML = `
        <span class="log-time">${t}</span>
        <span class="log-msg">${escapeHtml(message)}</span>
    `;

    container.insertBefore(entry, container.firstChild);

    while (container.children.length > 100) {
        container.removeChild(container.lastChild);
    }
}

// ── Data Table Dynamic Rows ───────────────────────────────────

function clearTableEmpty() {
    const empty = document.querySelector(".table-empty");
    if (empty) empty.closest("tr").remove();
}

function addTableRow(comment, flash) {
    clearTableEmpty();
    commentRowCount++;

    const tbody = document.getElementById("dataBody");
    const tr = document.createElement("tr");
    if (flash !== false) {
        tr.className = "flash";
        if (comment.username) {
            showToast(`Komentar terdeteksi dari @${comment.username}`, "success");
        }
    }

    const username = comment.username || "-";
    const text = comment.comment_text || comment.text || "";
    const ts = comment.comment_timestamp || "";
    const detected = comment.detected_at || "";
    const cycle = comment.cycle_number || "0";
    const source = comment.source || "";

    let tsDisplay = ts;
    let detectedDisplay = "";
    if (detected) {
        try {
            const d = new Date(detected);
            detectedDisplay = d.toLocaleString("id-ID", {
                day: '2-digit', month: '2-digit', year: 'numeric',
                hour: '2-digit', minute: '2-digit', second: '2-digit',
                hour12: false
            });
        } catch (e) { detectedDisplay = detected; }
    }

    // Tag label based on source
    const tagClass = source === "initial" ? "tag-initial" : "tag-new";
    const tagLabel = source === "initial" ? "AWAL" : "NEW";

    tr.innerHTML = `
        <td>${commentRowCount}</td>
        <td class="col-user">
            @${escapeHtml(username)}
            <span class="${tagClass}">${tagLabel}</span>
        </td>
        <td class="col-text">${escapeHtml(text)}</td>
        <td class="col-time">${escapeHtml(tsDisplay)}</td>
        <td class="col-time">${escapeHtml(detectedDisplay)}</td>
        <td class="col-cycle">${cycle === "0" ? "Awal" : "#" + escapeHtml(String(cycle))}</td>
    `;

    if (tbody.firstChild) {
        tbody.insertBefore(tr, tbody.firstChild);
    } else {
        tbody.appendChild(tr);
    }
}

// ── Countdown Timer Ticker ────────────────────────────────────

function startCountdown(seconds) {
    stopCountdown();
    countdownSeconds = seconds;
    showCountdown();

    countdownInterval = setInterval(function () {
        countdownSeconds--;
        if (countdownSeconds <= 0) {
            stopCountdown();
            document.getElementById("countdownTimer").textContent = "Scraping...";
        } else {
            showCountdown();
        }
    }, 1000);
}

function stopCountdown() {
    if (countdownInterval) {
        clearInterval(countdownInterval);
        countdownInterval = null;
    }
    document.getElementById("countdownTimer").textContent = "00:00";
}

function showCountdown() {
    const m = Math.floor(countdownSeconds / 60);
    const s = countdownSeconds % 60;
    document.getElementById("countdownTimer").textContent =
        String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
}

// ── Chart Redraw Ticker ───────────────────────────────────────

function updateChartDisplay() {
    cycleChart.data.labels = cycleChartData.map(d => "Siklus " + d.cycle);
    cycleChart.data.datasets[0].data = cycleChartData.map(d => d.new_found);
    cycleChart.update();
}

// ── Socket.IO Dynamic Listener Hooks ──────────────────────────

socket.on("log", function (data) {
    addLogEntry(data.message, data.level);

    if (data.level === "error") {
        showToast(data.message, "error");
    } else if (data.level === "warning") {
        showToast(data.message, "warning");
    }
});

socket.on("status_change", function (data) {
    updateDashboard(data);
});

socket.on("new_comment", function (comment) {
    addTableRow(comment, true);
});

socket.on("cycle_complete", function (data) {
    cycleChartData.push({ cycle: data.cycle, new_found: data.new_found || 0 });
    updateChartDisplay();
    showToast(`Siklus #${data.cycle} selesai: ${data.new_found} komentar baru.`, "success");
});

socket.on("countdown_start", function (data) {
    startCountdown(data.seconds);
});

// ── Utility Functions ─────────────────────────────────────────

function escapeHtml(text) {
    if (text === null || text === undefined) return "";
    const d = document.createElement("div");
    d.textContent = String(text);
    return d.innerHTML;
}
