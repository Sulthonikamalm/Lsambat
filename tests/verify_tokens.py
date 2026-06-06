"""
verify_tokens.py — Cek validitas semua token APIFY_TOKEN_* di .env.

Memakai endpoint Apify GET /users/me (GRATIS, tidak makan credit actor) untuk
verifikasi bahwa tiap token:
  - valid (auth OK)
  - punya quota tersisa bulan ini (monthlyUsageUsd vs limit)

Jalankan sebelum demo: python tests/verify_tokens.py
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import requests
from config import load_settings, get_apify_tokens


def check_token(idx: int, token: str) -> dict:
    """Return {valid, name, monthly_usage, monthly_limit, error}."""
    try:
        resp = requests.get(
            "https://api.apify.com/v2/users/me",
            params={"token": token},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            limits = data.get("limits", {}) or {}
            usage = data.get("currentBillingPeriodUsage", {}) or {}
            plan = data.get("plan", {}) or {}
            # plan bisa berupa string (id lama) atau dict (id baru)
            plan_id = plan.get("id", "?") if isinstance(plan, dict) else str(plan)
            monthly_usd = usage.get("monthlyUsageUsd", 0) or 0
            limit_usd = (
                (plan.get("maxMonthlyUsageUsd") if isinstance(plan, dict) else None)
                or limits.get("monthlyUsageUsd")
                or limits.get("monthlyUsdLimit")
                or 5  # default free tier
            )
            return {
                "valid": True,
                "username": data.get("username", "?"),
                "plan": plan_id,
                "monthly_usage_usd": round(float(monthly_usd), 4),
                "monthly_limit_usd": round(float(limit_usd), 2),
            }
        return {"valid": False, "status": resp.status_code, "body": resp.text[:200]}
    except Exception as e:
        return {"valid": False, "error": str(e)[:200]}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("=" * 70)
    print("  VERIFIKASI TOKEN APIFY")
    print("=" * 70)

    settings = load_settings()
    tokens = get_apify_tokens(settings)
    print(f"Ditemukan {len(tokens)} token aktif di .env.\n")

    valid_count = 0
    total_remaining = 0.0
    for i, tok in enumerate(tokens, start=1):
        r = check_token(i, tok)
        masked = tok[:14] + "..." + tok[-4:]
        if r["valid"]:
            valid_count += 1
            remaining = max(0.0, r["monthly_limit_usd"] - r["monthly_usage_usd"])
            total_remaining += remaining
            print(
                f"  [OK]   Token #{i} ({masked}) "
                f"user={r['username']} plan={r['plan']}"
            )
            print(
                f"         Pemakaian bulan ini: "
                f"${r['monthly_usage_usd']} / ${r['monthly_limit_usd']} "
                f"(sisa ${remaining:.4f})"
            )
        else:
            err = r.get("error") or f"HTTP {r.get('status')}: {r.get('body','')}"
            print(f"  [FAIL] Token #{i} ({masked}) — {err}")

    print("\n" + "=" * 70)
    print(
        f"  HASIL: {valid_count}/{len(tokens)} token valid, "
        f"total kuota tersisa ~${total_remaining:.4f}"
    )
    print("=" * 70)

    return 0 if valid_count == len(tokens) else 1


if __name__ == "__main__":
    sys.exit(main())
