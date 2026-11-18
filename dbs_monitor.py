import json
import os
import random
import time
import requests

# Fallback webhook plus dedicated channel webhooks
WEBHOOK_DEFAULT = os.environ.get("WEBHOOK_URL", "").strip()
WEBHOOK_DBS_BOXES = os.environ.get("WEBHOOK_DBS_BOXES", "").strip() or WEBHOOK_DEFAULT
WEBHOOK_DBS_PACKS = os.environ.get("WEBHOOK_DBS_PACKS", "").strip() or WEBHOOK_DEFAULT
WEBHOOK_PKMN_BOXES = os.environ.get("WEBHOOK_PKMN_BOXES", "").strip() or WEBHOOK_DEFAULT
WEBHOOK_PKMN_PACKS = os.environ.get("WEBHOOK_PKMN_PACKS", "").strip() or WEBHOOK_DEFAULT

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GIST_ID = os.environ.get("GIST_ID", "").strip()

if not any([WEBHOOK_DEFAULT, WEBHOOK_DBS_BOXES, WEBHOOK_DBS_PACKS, WEBHOOK_PKMN_BOXES, WEBHOOK_PKMN_PACKS]):
    raise RuntimeError("Missing Discord Webhook environment variables in Northflank!")

STATE_FILE = "seen_stock.json"

# Shopify stores covering both Dragon Ball and Pokemon sealed collections
STORES = [
    # Dragon Ball Collections
    "https://www.smokeandmirrorshobby.com/collections/dragon-ball-super-tcg-booster-boxes",
    "https://boardtopiagames.com/collections/dragon-ball-super",
    "https://overthebrick.com/collections/dragon-ball-super",
    "https://thehobhub.com/collections/dragon-ball-super",
    "https://www.gamescape.com/collections/dragon-ball-super-collectible-card-game",
    "https://guubuu.com/collections/dragon-ball-super-fusion-world",
    "https://www.cardshq.com/collections/dragon-ball-boxes",
    # Pokemon Collections
    "https://poke-collect.com/collections/pokemon-sealed-products",
    "https://www.smokeandmirrorshobby.com/collections/pokemon-elite-trainer-boxes",
    "https://www.smokeandmirrorshobby.com/collections/pokemon-booster-boxes",
    "https://boardtopiagames.com/collections/pokemon-sealed-product",
    "https://overthebrick.com/collections/pokemon",
]

DBS_KEYWORDS = [
    "fusion world", "dragon ball super", "dbs", "masters", "zenkai",
    "unison warrior", "ultra limit", "raging roar", "awakened pulse",
    "blazing aura", "new adventure", "cross force", "brightness of hope",
    "reach the god", "dual evolution", "saiyan's pride", "story booster",
    "manga booster", "fb0", "fb1", "fs0", "fs1", "sb0", "st01", "b2", "b3", "bt2", "bt3",
]

POKEMON_KEYWORDS = [
    "pokemon", "pokémon", "scarlet & violet", "mega evolution", "delta reign",
    "30th celebration", "pitch black", "chaos rising", "perfect order",
    "ascended heroes", "phantasmal flames", "prismatic evolutions",
    "surging sparks", "stellar crown", "shrouded fable", "twilight masquerade",
    "temporal forces", "paldean fates", "paradox rift", "151", "crown zenith",
    "elite trainer box", "etb", "booster bundle", "ultra-premium collection",
]

EXCLUDE_KEYWORDS = [
    "single", "psa", "bgs", "cgc", "alt art", "alternate art",
    "scr", "spr", "gdr", "slr", "playmat", "sleeve", "deck case",
    "binder", "bulk lot", "code card", "acrylic", "empty",
]

# Separate Deal and MSRP caps (USD) by Franchise and Product Category
THRESHOLDS = {
    "dbs": {
        "box": {"deal": 105.00, "msrp": 145.00},
        "starter": {"deal": 18.00, "msrp": 25.00},
        "pack": {"deal": 4.50, "msrp": 6.00},
    },
    "pokemon": {
        "box": {"deal": 115.00, "msrp": 161.00},
        "etb": {"deal": 42.00, "msrp": 59.99},
        "bundle": {"deal": 25.00, "msrp": 35.00},
        "starter": {"deal": 15.00, "msrp": 29.99},
        "pack": {"deal": 3.75, "msrp": 5.50},
    },
}


def gist_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def load_state():
    if GITHUB_TOKEN and GIST_ID:
        try:
            url = f"https://api.github.com/gists/{GIST_ID}"
            resp = requests.get(url, headers=gist_headers(), timeout=15)
            resp.raise_for_status()
            files = resp.json().get("files", {})
            if STATE_FILE in files and files[STATE_FILE].get("content"):
                data = json.loads(files[STATE_FILE]["content"])
                if isinstance(data, dict):
                    state = {str(k): float(v) for k, v in data.items()}
                    print(f"[Gist Sync] Loaded {len(state)} tracked items.")
                    return state
        except Exception as e:
            print(f"[Gist Load Warning] Falling back to local file: {e}")

    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {str(k): float(v) for k, v in data.items()}
        except Exception:
            pass
    return {}


def save_state(state):
    serialized = json.dumps(state, indent=2)
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write(serialized)
    except Exception as e:
        print(f"[Local Save Error] {e}")

    if GITHUB_TOKEN and GIST_ID:
        try:
            url = f"https://api.github.com/gists/{GIST_ID}"
            payload = {"files": {STATE_FILE: {"content": serialized}}}
            resp = requests.patch(url, headers=gist_headers(), json=payload, timeout=15)
            resp.raise_for_status()
            print(f"[Gist Sync] Saved {len(state)} items to GitHub Gist.")
        except Exception as e:
            print(f"[Gist Save Error] {e}")


def detect_game(title_lower, store_url_lower):
    if any(kw in title_lower for kw in DBS_KEYWORDS):
        return "dbs"
    if any(kw in title_lower for kw in POKEMON_KEYWORDS):
        return "pokemon"
    if "dragon-ball" in store_url_lower:
        return "dbs"
    if "pokemon" in store_url_lower or "poke-collect" in store_url_lower:
        return "pokemon"
    return None


def get_product_type(game, title_lower):
    is_multi_box = any(b in title_lower for b in ["booster box", "display", "case", "36 pack", "24 pack"])
    if is_multi_box:
        return "box"
    if game == "pokemon":
        if "elite trainer" in title_lower or "etb" in title_lower:
            return "etb"
        if "booster bundle" in title_lower or "6 pack" in title_lower:
            return "bundle"
    if "starter" in title_lower or "deck" in title_lower:
        return "starter"
    if any(w in title_lower for w in ["pack", "sleeved", "blister", "checklane"]) and "box" not in title_lower:
        return "pack"
    return "box"


def get_channel_webhook(game, prod_type):
    """Routes Boxes/ETBs/Bundles to the Boxes channel and Packs/Decks to the Packs channel."""
    is_pack_or_deck = prod_type in ("pack", "starter")
    if game == "dbs":
        return WEBHOOK_DBS_PACKS if is_pack_or_deck else WEBHOOK_DBS_BOXES
    return WEBHOOK_PKMN_PACKS if is_pack_or_deck else WEBHOOK_PKMN_BOXES


def classify_tier(game, prod_type, price):
    limits = THRESHOLDS[game][prod_type]
    if price <= limits["deal"]:
        return ("🔥 STEAL / UNDER MSRP", 3066993, True)
    if price <= limits["msrp"]:
        return ("✅ MSRP / GOOD PRICE", 16744192, True)
    return ("📦 ABOVE-MSRP RESTOCK", 16763904, False)


def send_startup_ping(tracked_count):
    target = WEBHOOK_DEFAULT or WEBHOOK_DBS_BOXES or WEBHOOK_PKMN_BOXES
    if not target:
        return
    sync_mode = "GitHub Gist Cloud Sync ✅" if (GITHUB_TOKEN and GIST_ID) else "Local Storage ⚠️"
    try:
        requests.post(
            target,
            json={
                "content": (
                    f"🟢 **Multi-Channel DBS & Pokémon Tracker online** — watching {len(STORES)} collections.\n"
                    f"💾 **Storage:** {sync_mode} ({tracked_count} items loaded)"
                )
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[Startup Ping Error] {e}")


def send_discord_alert(
    webhook_url, game, prod_type, badge, color, ping_everyone, store_domain,
    title, variant_title, price, compare_at, old_price, product_url, cart_url, image_url
):
    if not webhook_url:
        return

    game_label = "🐉 Dragon Ball TCG" if game == "dbs" else "⚡ Pokémon TCG"
    price_str = f"**${price:.2f}**"
    if old_price and old_price > price:
        drop_pct = ((old_price - price) / old_price) * 100
        price_str += f" ~~${old_price:.2f}~~ (-{drop_pct:.0f}%)"
    elif compare_at and compare_at > price:
        price_str += f" (Retail: ~~${compare_at:.2f}~~)"

    embed = {
        "title": f"{badge}: {title}",
        "url": product_url,
        "color": color,
        "fields": [
            {"name": "Game", "value": game_label, "inline": True},
            {"name": "Category", "value": prod_type.upper(), "inline": True},
            {"name": "Price", "value": price_str, "inline": True},
            {"name": "Store", "value": store_domain.replace("https://", ""), "inline": True},
            {"name": "Option", "value": variant_title or "Standard", "inline": True},
            {"name": "Fast Checkout", "value": f"[⚡ 1-Click Add to Cart]({cart_url})", "inline": False},
        ],
    }
    if image_url:
        embed["thumbnail"] = {"url": image_url}

    payload = {
        "content": "@everyone" if ping_everyone else f"🔔 *{game_label} Restock / Price Update*",
        "embeds": [embed],
    }
    requests.post(webhook_url, json=payload, timeout=10)


def check_store(base_url, state):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    clean_url = base_url.rstrip("/")
    store_url_lower = clean_url.lower()
    root_domain = clean_url.split("/collections/")[0]
    changed = False

    page = 1
    while page <= 5:
        endpoint = f"{clean_url}/products.json?limit=250&page={page}"
        resp = requests.get(endpoint, headers=headers, timeout=15)
        resp.raise_for_status()

        products = resp.json().get("products", [])
        if not products:
            break

        for product in products:
            title = product.get("title", "")
            title_lower = title.lower()

            if any(bad in title_lower for bad in EXCLUDE_KEYWORDS):
                continue

            game = detect_game(title_lower, store_url_lower)
            if not game:
                continue

            image_url = product["images"][0]["src"] if product.get("images") else None
            product_url = f"{root_domain}/products/{product.get('handle')}"
            prod_type = get_product_type(game, title_lower)
            msrp_limit = THRESHOLDS[game][prod_type]["msrp"]
            target_webhook = get_channel_webhook(game, prod_type)

            for variant in product.get("variants", []):
                variant_id = str(variant["id"])
                available = variant.get("available", False)
                try:
                    price = float(variant.get("price", 9999))
                    compare_raw = variant.get("compare_at_price")
                    compare_at = float(compare_raw) if compare_raw else None
                except (TypeError, ValueError):
                    continue

                if price <= 0:
                    continue

                cart_url = f"{root_domain}/cart/{variant_id}:1"

                if available:
                    if variant_id not in state:
                        badge, color, ping_everyone = classify_tier(game, prod_type, price)
                        print(f"[RESTOCK] [{game.upper()}-{prod_type.upper()}] {title} (${price:.2f})")
                        send_discord_alert(
                            target_webhook, game, prod_type, badge, color, ping_everyone,
                            root_domain, title, variant.get("title"), price, compare_at,
                            None, product_url, cart_url, image_url
                        )
                        state[variant_id] = price
                        changed = True
                    else:
                        old_price = state[variant_id]
                        dropped_5_pct = price <= (old_price * 0.95) and (old_price - price) >= 2.00
                        crossed_into_msrp = (old_price > msrp_limit) and (price <= msrp_limit)

                        if dropped_5_pct or crossed_into_msrp:
                            ping_everyone = price <= msrp_limit
                            badge = "📉 MSRP PRICE DROP" if ping_everyone else "📉 PRICE DROP (ABOVE MSRP)"
                            print(f"[PRICE DROP] [{game.upper()}] {title}: ${old_price:.2f} -> ${price:.2f}")
                            send_discord_alert(
                                target_webhook, game, prod_type, badge, 3447003, ping_everyone,
                                root_domain, title, variant.get("title"), price, compare_at,
                                old_price, product_url, cart_url, image_url
                            )
                            state[variant_id] = price
                            changed = True
                        elif price > old_price:
                            state[variant_id] = price
                            changed = True
                else:
                    if variant_id in state:
                        state.pop(variant_id, None)
                        changed = True

        if len(products) < 250:
            break
        page += 1
        time.sleep(1.2)

    return changed


if __name__ == "__main__":
    stock_state = load_state()
    print("Multi-channel DBS & Pokemon TCG Tracker is live...")
    send_startup_ping(len(stock_state))

    while True:
        any_changes = False
        for store in STORES:
            try:
                if check_store(store, stock_state):
                    any_changes = True
            except Exception as e:
                print(f"[Error] {store}: {e}")

        if any_changes:
            save_state(stock_state)

        time.sleep(random.uniform(20, 40))
