import csv
import os

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "resultats.csv")

HEADERS = [
    "Société",
    "Tél",
    "Secteur",
    "Adresse",
    "Ville",
    "Site Web",
    "Mail",
]


def ensure_header_written():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if not os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(HEADERS)


def append_row(row: dict):
    """row doit contenir les clés: nom, telephone, secteur, adresse,
    ville, site_web, email"""
    ensure_header_written()
    with open(OUTPUT_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                row.get("nom", ""),
                row.get("telephone", ""),
                row.get("secteur", ""),
                row.get("adresse", ""),
                row.get("ville", ""),
                row.get("site_web", ""),
                row.get("email", ""),
            ]
        )
