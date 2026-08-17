import re
from urllib.parse import urljoin

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

IGNORE_PATTERNS = [
    "sentry.io",
    "wixpress.com",
    "example.com",
    ".png",
    ".jpg",
    ".svg",
]


def extract_emails(text: str):
    found = set(EMAIL_REGEX.findall(text or ""))
    return [
        e for e in found if not any(p in e.lower() for p in IGNORE_PATTERNS)
    ]


def find_email_on_website(context, website_url: str, timeout_ms: int = 15000):
    if not website_url:
        return None

    page = None
    try:
        page = context.new_page()
        page.set_default_timeout(timeout_ms)

        page.goto(website_url, wait_until="domcontentloaded")
        html = page.content()
        emails = extract_emails(html)

        if not emails:
            # Essaye de trouver un lien "Contact" et de le suivre
            contact_link = page.locator("a", has_text=re.compile("contact", re.I)).first
            if contact_link.count() > 0:
                href = contact_link.get_attribute("href")
                if href:
                    url = urljoin(website_url, href)
                    page.goto(url, wait_until="domcontentloaded")
                    html = page.content()
                    emails = extract_emails(html)

        return emails[0] if emails else None
    except Exception:
        return None
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass
