import json
import os
import random
import time

from playwright.sync_api import sync_playwright

from config.sectors import SECTEURS
from config.villes import VILLES
from src.scraper import scrape_sector_in_city
from src.csv_writer import append_row

PROGRESS_PATH = os.path.join(os.path.dirname(__file__), "progress", "done.json")

# Budget de temps pour ce run (par défaut 5h). Sur GitHub Actions, laisse
# de la marge sous le timeout du job pour permettre le commit final.
TIME_BUDGET_SECONDS = int(os.environ.get("TIME_BUDGET_SECONDS", 5 * 60 * 60))

# En CI il n'y a pas d'écran -> headless obligatoire.
# En local tu peux garder False si tu veux voir le navigateur.
HEADLESS = os.environ.get("CI", "false").lower() == "true"


def load_progress():
    if not os.path.exists(PROGRESS_PATH):
        return set()
    try:
        with open(PROGRESS_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_progress(done_set):
    os.makedirs(os.path.dirname(PROGRESS_PATH), exist_ok=True)
    with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(done_set), f, ensure_ascii=False, indent=2)


def main():
    done = load_progress()
    total_combos = len(SECTEURS) * len(VILLES)
    combos_done = 0
    start_time = time.time()

    print(f"Déjà fait : {len(done)}/{total_combos}")
    print(f"Mode headless : {HEADLESS}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=HEADLESS,
        )
        context = browser.new_context(
            locale="fr-FR",
            viewport={"width": 1400, "height": 900},
        )

        stopped_early = False

        for ville in VILLES:
            for secteur in SECTEURS:
                key = f"{secteur}|||{ville}"
                combos_done += 1

                if key in done:
                    continue

                elapsed = time.time() - start_time
                if elapsed > TIME_BUDGET_SECONDS:
                    print("Budget de temps atteint, arrêt propre pour aujourd'hui.")
                    stopped_early = True
                    break

                print(f"[{combos_done}/{total_combos}] Scraping: {secteur} à {ville}...")

                try:
                    rows = scrape_sector_in_city(
                    context,
                    secteur,
                    ville,
                    max_results=1060,
                )
                    
                    for row in rows:
                        append_row(row)

                    print(f"  -> {len(rows)} fiches trouvées")
                    done.add(key)
                    save_progress(done)
                except Exception as e:
                    print(f"  -> ERREUR sur {secteur} / {ville}: {e}")

                time.sleep(3 + random.random() * 2)

            if stopped_early:
                break

        browser.close()

    remaining = total_combos - len(done)
    print(f"Terminé pour ce run. {len(done)}/{total_combos} combinaisons faites, {remaining} restantes.")

    if remaining == 0:
        with open(os.path.join(os.path.dirname(__file__), "SCRAPING_TERMINE"), "w") as f:
            f.write("done")


if __name__ == "__main__":
    main()
