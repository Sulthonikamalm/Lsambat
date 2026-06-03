/**
 * utils.js — Helper utilities, Modals, Toasts, Charts, and Logs
 */

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

document.addEventListener("DOMContentLoaded", () => {
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
