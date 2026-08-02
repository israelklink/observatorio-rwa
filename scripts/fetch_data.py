#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Coletor de dados do Observatório de RWA & Cripto.
Roda 1x por dia no GitHub Actions (ou localmente) e gera data/data.json.

Somente biblioteca padrão do Python (urllib) -> nenhuma dependência.
Fontes gratuitas e sem chave:
  - CoinPaprika    : mercado global, dominância, top 50 moedas, ativos "verdes"
  - DefiLlama      : TVL por rede + histórico, protocolos RWA (+ histórico por categoria), stablecoins
  - alternative.me : Índice de Medo & Ganância (Fear & Greed) + histórico
"""

import json, urllib.request, datetime, sys, os, bisect

UA = {"User-Agent": "observatorio-rwa/4.0 (+github-actions)"}
TIMEOUT = 90
HIST_POINTS = 180
RWA_HIST_PROTOCOLS = 40   # nº de protocolos RWA cujo histórico é somado por categoria

_TICKERS = None
CG_KEY = os.environ.get("COINGECKO_KEY", "").strip()  # chave Demo gratuita do CoinGecko (GitHub Secret)


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def safe(fn, label):
    try:
        return fn()
    except Exception as e:
        print(f"  [aviso] falha em {label}: {e}", file=sys.stderr)
        return None


def downsample(series):
    return series[-HIST_POINTS:]


def tickers():
    global _TICKERS
    if _TICKERS is None:
        _TICKERS = get("https://api.coinpaprika.com/v1/tickers?quotes=USD")
    return _TICKERS


def _usd(t):
    return (t.get("quotes") or {}).get("USD") or {}


# ---------------- MACRO ----------------
def build_macro():
    g = get("https://api.coinpaprika.com/v1/global")
    total = g.get("market_cap_usd") or 0
    macro = {"market_cap_usd": g.get("market_cap_usd"), "volume_24h_usd": g.get("volume_24h_usd"),
        "btc_dominance": g.get("bitcoin_dominance_percentage"),
        "cryptocurrencies_number": g.get("cryptocurrencies_number"),
        "market_cap_change_24h": g.get("market_cap_change_24h"),
        "eth_dominance": None, "top_coins": [], "dominance": []}
    ts = [t for t in tickers() if t.get("rank")]
    ts.sort(key=lambda t: t["rank"])
    for t in ts[:50]:
        q = _usd(t)
        macro["top_coins"].append({"name": t.get("name"), "symbol": t.get("symbol"),
            "price": q.get("price"), "market_cap": q.get("market_cap"),
            "change_24h": q.get("percent_change_24h")})
    eth = next((c for c in macro["top_coins"] if c["symbol"] == "ETH"), None)
    if eth and eth["market_cap"] and total:
        macro["eth_dominance"] = round(eth["market_cap"] / total * 100, 2)
    if total:
        acc = 0.0
        for c in macro["top_coins"][:6]:
            if c["market_cap"]:
                pct = c["market_cap"] / total * 100; acc += pct
                macro["dominance"].append({"symbol": c["symbol"], "pct": round(pct, 2)})
        macro["dominance"].append({"symbol": "Outros", "pct": round(max(0.0, 100 - acc), 2)})
    return macro


# ---------------- VERDES ----------------
GREEN = {"ETH": "pos", "ADA": "pos", "DOT": "pos", "ATOM": "pos", "FLOW": "pos",
    "ALGO": "carbon_negative", "CELO": "refi", "REGEN": "refi",
    "XTZ": "low_energy", "HBAR": "low_energy", "XLM": "low_energy",
    "AVAX": "low_energy", "MIOTA": "low_energy", "IOTA": "low_energy", "CHZ": "low_energy",
    "SOL": "efficient_pos", "EGLD": "efficient_pos", "NEAR": "carbon_neutral",
    "POL": "carbon_neutral", "MATIC": "carbon_neutral", "XNO": "feeless",
    "XCH": "eco_farming", "KLIMA": "carbon", "EWT": "energy"}

# Mecanismo de consenso (curado) de cada ativo verde.
CONSENSUS = {
    "ETH": "PoS", "ADA": "Ouroboros PoS", "DOT": "NPoS", "ATOM": "Tendermint PoS", "FLOW": "PoS",
    "ALGO": "Pure PoS", "CELO": "PoS", "REGEN": "Tendermint PoS", "XTZ": "Liquid PoS",
    "HBAR": "Hashgraph aBFT", "XLM": "Stellar (FBA)", "AVAX": "Avalanche PoS",
    "MIOTA": "Tangle (DAG)", "IOTA": "Tangle (DAG)", "CHZ": "PoS", "SOL": "PoH + PoS",
    "EGLD": "Secure PoS", "NEAR": "PoS", "POL": "PoS", "MATIC": "PoS", "XNO": "ORV",
    "XCH": "Proof of Space", "KLIMA": "PoS (Polygon)", "EWT": "PoA",
}

def build_green():
    best = {}
    for t in tickers():
        sym = t.get("symbol")
        if sym in GREEN:
            q = _usd(t)
            mc = q.get("market_cap")
            if mc and (sym not in best or mc > best[sym]["market_cap"]):
                best[sym] = {"name": t.get("name"), "symbol": sym, "reason": GREEN[sym],
                    "consensus": CONSENSUS.get(sym),
                    "price": q.get("price"), "market_cap": mc,
                    "change_24h": q.get("percent_change_24h"),
                    "change_7d": q.get("percent_change_7d"),
                    "volume_24h": q.get("volume_24h"),
                    "circulating_supply": t.get("circulating_supply"),
                    "max_supply": t.get("max_supply")}
    lst = sorted(best.values(), key=lambda a: a["market_cap"], reverse=True)
    return {"total_mcap": sum(a["market_cap"] for a in lst), "count": len(lst), "assets": lst}


# ---------------- MEDO & GANÂNCIA ----------------
def build_fng():
    d = get("https://api.alternative.me/fng/?limit=60&format=json")
    arr = d.get("data") or []
    if not arr:
        return None
    return {"value": int(arr[0]["value"]), "classification": arr[0].get("value_classification"),
            "history": [[int(x["timestamp"]) * 1000, int(x["value"])] for x in reversed(arr)]}


# ---------------- RWA (+ categoria + histórico por categoria) ----------------
RWA_MAP = [
    ("treasuries", ["ondo", "buidl", "superstate", "openeden", "hashnote", "matrixdock",
        "spiko", "tbill", "t-bill", "treasur", "franklin", "benji", "wisdomtree",
        "mountain", "usdy", "usyc", "ustb", "midas", "usual"]),
    ("private_credit", ["maple", "goldfinch", "centrifuge", "credix", "clearpool", "truefi",
        "huma", "jia", "untangled", "credit"]),
    ("real_estate", ["realt", "tangible", "propy", "landshare", "estate", "lofty", "reinno"]),
    ("commodities", ["paxg", "pax gold", "tether gold", "xaut", "kinesis", "gold",
        "commodit", "cache", "silver"]),
    ("equities", ["dinari", "swarm", "stock", "equit", "backed"]),
]

def classify_rwa(name):
    n = (name or "").lower()
    for cat, keys in RWA_MAP:
        if any(k in n for k in keys):
            return cat
    return "other"


# ---- Classes de ativo RWA via categorias do CoinGecko (valor de mercado) ----
def cg_get(path):
    url = "https://api.coingecko.com/api/v3" + path
    if CG_KEY:
        url += ("&" if "?" in path else "?") + "x_cg_demo_api_key=" + CG_KEY
    return get(url)

def _cg_class(name, cid):
    s = (name or "").lower() + " " + (cid or "").lower()
    if not ("tokeniz" in s or "real world" in s or "rwa" in s):
        return None
    for key, kws in [("treasuries", ["treasur"]), ("gold", ["gold"]),
                     ("stocks", ["stock", "equit"]),
                     ("real_estate", ["real estate", "real-estate"]),
                     ("commodities", ["commodit"])]:
        if any(k in s for k in kws):
            return key
    return None

def build_asset_classes():
    cats = cg_get("/coins/categories")
    classes = {}
    umbrella = None
    for c in cats:
        cid, name, mc = c.get("id"), c.get("name"), c.get("market_cap")
        low = (name or "").lower() + " " + (cid or "").lower()
        if (cid == "real-world-assets-rwa" or "real world assets" in low) and mc:
            umbrella = mc if (umbrella is None or mc > umbrella) else umbrella
        k = _cg_class(name, cid)
        if k and mc and (k not in classes or mc > classes[k]["market_cap"]):
            classes[k] = {"cat": k, "name": name, "market_cap": mc,
                          "change_24h": c.get("market_cap_change_24h")}
    return {"umbrella_mcap": umbrella,
            "classes": sorted(classes.values(), key=lambda x: x["market_cap"], reverse=True)}


def build_rwa_history(subset, cat_order):
    """Soma o TVL histórico dos protocolos por categoria, num eixo diário de ~180 dias."""
    day = 86400
    today = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) // day * day
    axis = [today - (HIST_POINTS - 1 - i) * day for i in range(HIST_POINTS)]
    idx = {c: i for i, c in enumerate(cat_order)}
    vals = [[0.0] * len(cat_order) for _ in axis]
    for p in subset:
        slug = p.get("slug")
        cat = classify_rwa(p.get("name"))
        ci = idx.get(cat)
        if not slug or ci is None:
            continue
        hist = safe(lambda: get("https://api.llama.fi/protocol/" + slug), "hist " + slug)
        tv = (hist or {}).get("tvl") or []
        if not tv:
            continue
        dates = [int(x["date"]) for x in tv]
        pvals = [x.get("totalLiquidityUSD") or 0 for x in tv]
        for ai, dday in enumerate(axis):
            j = bisect.bisect_right(dates, dday) - 1   # carry-forward: último ponto <= dia
            if j >= 0:
                vals[ai][ci] += pvals[j]
    series = [[axis[i] * 1000, [round(v) for v in vals[i]]] for i in range(len(axis))]
    return {"cats": cat_order, "series": series}


def build_rwa():
    protocols = get("https://api.llama.fi/protocols")
    rwa = [p for p in protocols if (p.get("category") == "RWA") and p.get("tvl")]
    rwa.sort(key=lambda p: p.get("tvl") or 0, reverse=True)
    total = sum((p.get("tvl") or 0) for p in rwa)
    top = [{"name": p.get("name"), "tvl": p.get("tvl"), "cat": classify_rwa(p.get("name")),
            "chains": (p.get("chains") or [])[:3], "change_1d": p.get("change_1d")} for p in rwa[:15]]
    cats = {}
    for p in rwa:
        d = cats.setdefault(classify_rwa(p.get("name")), {"cat": "", "tvl": 0.0, "count": 0})
        d["cat"] = classify_rwa(p.get("name")); d["tvl"] += p.get("tvl") or 0; d["count"] += 1
    categories = sorted(cats.values(), key=lambda x: x["tvl"], reverse=True)
    hbc = safe(lambda: build_rwa_history(rwa[:RWA_HIST_PROTOCOLS], [c["cat"] for c in categories]),
               "rwa history_by_cat")
    ac = safe(build_asset_classes, "asset classes")
    return {"total_tvl": total, "count": len(rwa), "protocols": top,
            "categories": categories, "history_by_cat": hbc, "asset_classes": ac}


# ---------------- STABLECOINS ----------------
def build_stables():
    data = get("https://stablecoins.llama.fi/stablecoins?includePrices=false")
    arr = data.get("peggedAssets") or []
    def cap(a): return ((a.get("circulating") or {}).get("peggedUSD")) or 0
    total = sum(cap(a) for a in arr)
    arr.sort(key=cap, reverse=True)
    top = [{"name": a.get("name"), "symbol": a.get("symbol"), "cap": cap(a),
            "pegType": a.get("pegType")} for a in arr[:15]]
    history = None
    h = safe(lambda: get("https://stablecoins.llama.fi/stablecoincharts/all"), "stablecoin history")
    if h:
        series = [[int(pt["date"]) * 1000, ((pt.get("totalCirculatingUSD") or {}).get("peggedUSD"))]
                  for pt in h if (pt.get("totalCirculatingUSD") or {}).get("peggedUSD") is not None]
        history = downsample(series)
    return {"total": total, "count": len(arr), "top": top, "history": history}


# ---------------- DEFI ----------------
def build_defi(stables_total):
    chains = [c for c in get("https://api.llama.fi/v2/chains") if c.get("tvl")]
    chains.sort(key=lambda c: c["tvl"], reverse=True)
    total = sum(c["tvl"] for c in chains)
    top = [{"name": c.get("name"), "tvl": c.get("tvl")} for c in chains[:15]]
    history = None
    h = safe(lambda: get("https://api.llama.fi/v2/historicalChainTvl"), "tvl history")
    if h:
        history = downsample([[int(pt["date"]) * 1000, pt.get("tvl")] for pt in h])
    return {"total_tvl": total, "chains_count": len(chains), "chains": top,
            "stablecoins_total": stables_total, "tvl_history": history}


def main():
    print("Coletando dados...")
    stables = safe(build_stables, "stables")
    stables_total = stables["total"] if stables else None
    data = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "macro": safe(build_macro, "macro"),
        "green": safe(build_green, "green"),
        "fng": safe(build_fng, "fng"),
        "rwa": safe(build_rwa, "rwa"),
        "stables": stables,
        "defi": safe(lambda: build_defi(stables_total), "defi"),
    }
    os.makedirs("data", exist_ok=True)
    with open("data/data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    m, g, fng, r, s, d = (data["macro"], data["green"], data["fng"],
                          data["rwa"], data["stables"], data["defi"])
    print("OK -> data/data.json")
    if m: print(f"  macro: cap ${ (m['market_cap_usd'] or 0)/1e12:.2f}T | {len(m['top_coins'])} moedas")
    if g: print(f"  green: {g['count']} ativos | ${ (g['total_mcap'] or 0)/1e9:.2f}B")
    if fng: print(f"  fng:   {fng['value']} ({fng['classification']})")
    if r:
        hb = (r.get('history_by_cat') or {}).get('series') or []
        print(f"  rwa:   {r['count']} protocolos | {len(r['categories'])} categorias | hist {len(hb)} pts")
    if s: print(f"  stab:  {s['count']} stablecoins | hist {len(s['history'] or [])} pts")
    if d: print(f"  defi:  {d['chains_count']} redes | hist {len(d['tvl_history'] or [])} pts")
    if not any([m, g, fng, r, s, d]):
        print("ERRO: nenhuma fonte retornou dados.", file=sys.stderr); sys.exit(1)


if __name__ == "__main__":
    main()
