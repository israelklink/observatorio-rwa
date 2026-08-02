#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Coletor de dados do Observatório de RWA & Cripto.
Roda 1x por dia no GitHub Actions (ou localmente) e gera data/data.json.

Somente biblioteca padrão do Python (urllib) -> nenhuma dependência.
Fontes gratuitas e sem chave:
  - CoinPaprika : mercado global, dominância, top 50 moedas
  - DefiLlama   : TVL por rede + histórico, protocolos RWA, stablecoins + histórico
"""

import json, urllib.request, datetime, sys, os

UA = {"User-Agent": "observatorio-rwa/2.0 (+github-actions)"}
TIMEOUT = 90
HIST_POINTS = 1500  # ~4 anos de pontos diários (permite os intervalos 1M/3M/6M/1Y/ALL)


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
    """Recebe [[date_ms, value], ...]; mantém os últimos HIST_POINTS pontos."""
    return series[-HIST_POINTS:]


# ---------------- MACRO (CoinPaprika) ----------------
def build_macro():
    g = get("https://api.coinpaprika.com/v1/global")
    total = g.get("market_cap_usd") or 0
    macro = {
        "market_cap_usd": g.get("market_cap_usd"),
        "volume_24h_usd": g.get("volume_24h_usd"),
        "btc_dominance": g.get("bitcoin_dominance_percentage"),
        "cryptocurrencies_number": g.get("cryptocurrencies_number"),
        "market_cap_change_24h": g.get("market_cap_change_24h"),
        "eth_dominance": None, "top_coins": [], "dominance": [],
    }
    tickers = [t for t in get("https://api.coinpaprika.com/v1/tickers?quotes=USD") if t.get("rank")]
    tickers.sort(key=lambda t: t["rank"])
    for t in tickers[:50]:
        q = (t.get("quotes") or {}).get("USD") or {}
        macro["top_coins"].append({
            "name": t.get("name"), "symbol": t.get("symbol"),
            "price": q.get("price"), "market_cap": q.get("market_cap"),
            "change_24h": q.get("percent_change_24h"),
        })
    eth = next((c for c in macro["top_coins"] if c["symbol"] == "ETH"), None)
    if eth and eth["market_cap"] and total:
        macro["eth_dominance"] = round(eth["market_cap"] / total * 100, 2)
    if total:
        acc = 0.0
        for c in macro["top_coins"][:6]:
            if c["market_cap"]:
                pct = c["market_cap"] / total * 100
                acc += pct
                macro["dominance"].append({"symbol": c["symbol"], "pct": round(pct, 2)})
        macro["dominance"].append({"symbol": "Outros", "pct": round(max(0.0, 100 - acc), 2)})
    return macro


# ---------------- RWA (DefiLlama) + categoria curada ----------------
# Mapeamento curado (aproximado): palavra-chave no nome -> categoria.
RWA_MAP = [
    ("treasuries", ["ondo", "buidl", "superstate", "openeden", "hashnote", "matrixdock",
                     "spiko", "tbill", "t-bill", "treasur", "franklin", "benji", "wisdomtree",
                     "mountain", "usdy", "usyc", "ustb", "backed fi", "midas"]),
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

def build_rwa_history(rwa_protocols):
    """Monta a evolução do TVL de RWA por categoria (para o gráfico de área empilhada).
    Usa o histórico de cada protocolo (endpoint gratuito /protocol/{slug}), classifica em
    categoria e soma por dia. Retorna {cats:[...], series:[[date_ms,[v_cat1,...]], ...]}."""
    sel = rwa_protocols[:40]  # os 40 maiores já cobrem ~99% do TVL total de RWA
    per_proto, all_days = [], set()
    for p in sel:
        slug = p.get("slug") or (p.get("name") or "").lower().replace(" ", "-")
        cat = classify_rwa(p.get("name"))
        d = safe(lambda: get(f"https://api.llama.fi/protocol/{slug}"), f"hist rwa {slug}")
        if not d:
            continue
        series = {}
        for pt in (d.get("tvl") or []):
            ts, v = pt.get("date"), pt.get("totalLiquidityUSD")
            if ts is None or v is None:
                continue
            day = int(ts) - (int(ts) % 86400)  # arredonda para o início do dia (UTC)
            series[day] = v                    # último valor do dia prevalece
            all_days.add(day)
        if series:
            per_proto.append((cat, series))
    if not all_days:
        return None
    days = sorted(all_days)
    day_cat = {d: {} for d in days}
    cats_seen = {}
    for cat, series in per_proto:
        first = min(series)
        last_val = None
        for d in days:                         # repete o último valor conhecido (forward-fill)
            if d < first:
                continue
            if d in series:
                last_val = series[d]
            if last_val is not None:
                day_cat[d][cat] = day_cat[d].get(cat, 0.0) + last_val
        cats_seen[cat] = True
    last_day = days[-1]
    cats = sorted(cats_seen, key=lambda c: day_cat[last_day].get(c, 0), reverse=True)
    series_out = [[d * 1000, [round(day_cat[d].get(c, 0.0)) for c in cats]] for d in days]
    return {"cats": cats, "series": downsample(series_out)}


def build_rwa():
    protocols = get("https://api.llama.fi/protocols")
    rwa = [p for p in protocols if (p.get("category") == "RWA") and p.get("tvl")]
    rwa.sort(key=lambda p: p.get("tvl") or 0, reverse=True)
    total = sum((p.get("tvl") or 0) for p in rwa)
    top = [{"name": p.get("name"), "tvl": p.get("tvl"),
            "chains": (p.get("chains") or [])[:3], "change_1d": p.get("change_1d"),
            "cat": classify_rwa(p.get("name"))} for p in rwa[:30]]
    cats = {}
    for p in rwa:
        c = classify_rwa(p.get("name"))
        d = cats.setdefault(c, {"cat": c, "tvl": 0.0, "count": 0})
        d["tvl"] += p.get("tvl") or 0
        d["count"] += 1
    categories = sorted(cats.values(), key=lambda x: x["tvl"], reverse=True)
    hist = safe(lambda: build_rwa_history(rwa), "rwa history")
    return {"total_tvl": total, "count": len(rwa), "protocols": top,
            "categories": categories, "history_by_cat": hist}


# ---------------- STABLECOINS (DefiLlama) ----------------
def build_stables():
    data = get("https://stablecoins.llama.fi/stablecoins?includePrices=false")
    arr = data.get("peggedAssets") or []
    def cap(a): return ((a.get("circulating") or {}).get("peggedUSD")) or 0
    total = sum(cap(a) for a in arr)
    arr.sort(key=cap, reverse=True)
    top = [{"name": a.get("name"), "symbol": a.get("symbol"),
            "cap": cap(a), "pegType": a.get("pegType")} for a in arr[:15]]
    history = None
    h = safe(lambda: get("https://stablecoins.llama.fi/stablecoincharts/all"), "stablecoin history")
    if h:
        series = []
        for pt in h:
            v = ((pt.get("totalCirculatingUSD") or {}).get("peggedUSD"))
            if v is not None:
                series.append([int(pt["date"]) * 1000, v])
        history = downsample(series)
    return {"total": total, "count": len(arr), "top": top, "history": history}


# ---------------- DEFI (DefiLlama) ----------------
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
        "rwa": safe(build_rwa, "rwa"),
        "stables": stables,
        "defi": safe(lambda: build_defi(stables_total), "defi"),
    }
    os.makedirs("data", exist_ok=True)
    with open("data/data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    m, r, s, d = data["macro"], data["rwa"], data["stables"], data["defi"]
    print("OK -> data/data.json")
    if m: print(f"  macro: cap ${ (m['market_cap_usd'] or 0)/1e12:.2f}T | {len(m['top_coins'])} moedas")
    if r: print(f"  rwa:   {r['count']} protocolos | {len(r['categories'])} categorias")
    if s: print(f"  stab:  {s['count']} stablecoins | ${ (s['total'] or 0)/1e9:.2f}B | hist {len(s['history'] or [])} pts")
    if d: print(f"  defi:  {d['chains_count']} redes | hist {len(d['tvl_history'] or [])} pts")
    if not any([m, r, s, d]):
        print("ERRO: nenhuma fonte retornou dados.", file=sys.stderr); sys.exit(1)


if __name__ == "__main__":
    main()
