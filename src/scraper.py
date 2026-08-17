import re
import time
import random
from urllib.parse import quote

from .email_finder import find_email_on_website


def clean_text(text: str) -> str:
    """Retire les retours à la ligne / espaces parasites en début et fin,
    et normalise les espaces multiples."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def extract_postal_code(adresse: str) -> str:
    if not adresse:
        return ""
    match = re.search(r"\b\d{4,5}\b", adresse)
    return match.group(0) if match else ""


def guess_city_from_address(adresse: str, fallback_city: str) -> str:
    """L'adresse Google Maps a le format 'Rue X, CODE_POSTAL Ville, Pays'.
    On cherche donc le segment qui contient le code postal, et on garde
    le texte après le code postal comme nom de ville."""
    if not adresse:
        return fallback_city

    match = re.search(r"\b\d{4,5}\s+([^,]+)", adresse)
    if match:
        return clean_text(match.group(1))

    return fallback_city


def scroll_results_panel(page, max_scrolls: int = 25):
    feed_selector = 'div[role="feed"]'
    try:
        page.wait_for_selector(feed_selector, timeout=20000)
    except Exception:
        return

    previous_count = 0
    for i in range(max_scrolls):
        cards = page.locator(f"{feed_selector} > div > div[jsaction]").count()
        if cards == previous_count and i > 3:
            break
        previous_count = cards

        page.evaluate(
            """(sel) => {
                const feed = document.querySelector(sel);
                if (feed) feed.scrollTop = feed.scrollHeight;
            }""",
            feed_selector,
        )
        time.sleep(1.2 + random.random() * 0.8)


def scrape_sector_in_city(context, secteur: str, ville: str, max_results: int = 60, enrich_email: bool = True):
    page = context.new_page()
    query = f"{secteur} à {ville}"
    url = f"https://www.google.com/maps/search/{quote(query)}"
    results = []

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)

        # Accepter cookies si la bannière apparaît
        consent_btn = page.locator(
            'button:has-text("Tout accepter"), button:has-text("Accept all")'
        ).first
        if consent_btn.count() > 0:
            try:
                consent_btn.click()
                page.wait_for_timeout(1000)
            except Exception:
                pass

        scroll_results_panel(page)

        card_links = page.locator('div[role="feed"] a[href*="/maps/place/"]').all()
        unique_hrefs = []
        for link in card_links:
            try:
                href = link.get_attribute("href")
            except Exception:
                href = None
            if href and href not in unique_hrefs:
                unique_hrefs.append(href)
            if len(unique_hrefs) >= max_results:
                break

        for href in unique_hrefs:
            detail_page = context.new_page()
            try:
                detail_page.goto(href, wait_until="domcontentloaded", timeout=20000)
                detail_page.wait_for_timeout(1500)

                nom = clean_text(_safe_text(detail_page.locator("h1").first))
                telephone = clean_text(_safe_text(
                    detail_page.locator('button[data-item-id^="phone:"]').first
                ))
                site_web = clean_text(_safe_attr(
                    detail_page.locator('a[data-item-id="authority"]').first, "href"
                ))
                adresse = clean_text(_safe_text(
                    detail_page.locator('button[data-item-id="address"]').first
                ))
                categorie = clean_text(_safe_text(
                    detail_page.locator('button[jsaction*="category"]').first
                )) or secteur

                email = None
                if enrich_email and site_web:
                    email = find_email_on_website(context, site_web)

                results.append(
                    {
                        "nom": nom or "",
                        "secteur": categorie,
                        "telephone": telephone or "",
                        "site_web": site_web or "",
                        "adresse": adresse or "",
                        "ville": guess_city_from_address(adresse, ville),
                        "code_postal": extract_postal_code(adresse),
                        "email": email or "",
                    }
                )
            except Exception:
                pass
            finally:
                try:
                    detail_page.close()
                except Exception:
                    pass
    finally:
        try:
            page.close()
        except Exception:
            pass

    return results


def _safe_text(locator):
    try:
        return locator.inner_text()
    except Exception:
        return ""


def _safe_attr(locator, attr):
    try:
        return locator.get_attribute(attr)
    except Exception:
        return ""
