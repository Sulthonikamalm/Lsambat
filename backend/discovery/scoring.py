"""
discovery/scoring.py — Relevance scoring logic.
"""

DEFAULT_KEYWORDS = [
    "banjir", "jalan rusak", "macet", "sampah", "pdam", "air mati",
    "lampu jalan", "pju", "drainase", "parkir", "trotoar", "pelayanan",
    "puskesmas", "pemkot", "dishub", "dlh", "bpbd", "wargaku",
    "keluhan", "lapor", "tolong", "rusak", "lubang", "aduan", "hotline",
    "jukir", "liar", "pembongkaran", "penertiban",
]


def score_post_relevance(
    caption: str, priority_level: str,
    comment_count: int, post_age_days: int,
    relevance_settings: dict = None,
) -> dict:
    """
    Skor relevansi multi-faktor. Return dict lengkap agar transparan.

    Faktor:
    1. Keyword match (0-40): berapa kata keluhan yang cocok di caption
    2. Prioritas akun (0-25): akun pemerintah vs komunitas
    3. Engagement/komentar (0-20): banyak komentar = banyak warga merespons
    4. Umur postingan (0-15): postingan baru lebih relevan

    Total: 0-100
    """
    if relevance_settings is None:
        relevance_settings = {}

    # Load config atau fallback
    keywords = relevance_settings.get("keywords", DEFAULT_KEYWORDS)
    weights = relevance_settings.get("weights", {})
    thresholds = relevance_settings.get("thresholds", {})

    kw_per_match = weights.get("keyword_per_match", 10)
    kw_max = weights.get("keyword_max", 40)
    p1_score = weights.get("priority_level_1", 25)
    p2_score = weights.get("priority_level_2", 15)
    p3_score = weights.get("priority_level_3", 5)
    eng_50 = weights.get("engagement_50_plus", 20)
    eng_20 = weights.get("engagement_20_plus", 15)
    eng_5 = weights.get("engagement_5_plus", 10)
    rec_7 = weights.get("recency_7_days", 15)
    rec_14 = weights.get("recency_14_days", 10)
    rec_30 = weights.get("recency_30_days", 5)
    th_high = thresholds.get("high", 60)
    th_medium = thresholds.get("medium", 30)

    score = 0
    reasons = []

    # Jika caption kosong
    if not caption or not caption.strip():
        reasons.append("caption tidak tersedia")
        # Tetap hitung faktor lain
    else:
        # Faktor 1: Keyword match
        caption_lower = caption.lower()
        matched = [kw for kw in keywords if kw in caption_lower]
        keyword_score = min(len(matched) * kw_per_match, kw_max)
        score += keyword_score
        if matched:
            kw_display = ", ".join(matched[:4])
            if len(matched) > 4:
                kw_display += f" (+{len(matched)-4} lagi)"
            reasons.append(f"kata keluhan: {kw_display} (+{keyword_score})")
        else:
            reasons.append("tidak ada kata keluhan (+0)")

    # Faktor 2: Prioritas akun
    try:
        pl = int(priority_level)
    except (ValueError, TypeError):
        pl = 3
    priority_map = {1: p1_score, 2: p2_score, 3: p3_score}
    priority_score = priority_map.get(pl, 0)
    score += priority_score
    reasons.append(f"prioritas akun: level {pl} (+{priority_score})")

    # Faktor 3: Engagement (jumlah komentar)
    if comment_count >= 50:
        engagement_score = eng_50
    elif comment_count >= 20:
        engagement_score = eng_20
    elif comment_count >= 5:
        engagement_score = eng_5
    else:
        engagement_score = 0
    score += engagement_score
    reasons.append(f"komentar: {comment_count} (+{engagement_score})")

    # Faktor 4: Recency (umur postingan)
    if post_age_days <= 7:
        recency_score = rec_7
    elif post_age_days <= 14:
        recency_score = rec_14
    elif post_age_days <= 30:
        recency_score = rec_30
    else:
        recency_score = 0
    score += recency_score
    if post_age_days < 999:
        reasons.append(f"umur: {post_age_days} hari (+{recency_score})")
    else:
        reasons.append(f"umur: tidak diketahui (+{recency_score})")

    # Tentukan label
    if score >= th_high:
        label = "high"
    elif score >= th_medium:
        label = "medium"
    else:
        label = "low"

    return {
        "score": score,
        "label": label,
        "reasons": reasons,
        "matched_keywords": matched if caption and caption.strip() else [],
    }
