# GMaps Scraper — B2B / B2C (version Python)

Scraper Google Maps pour extraire : Nom, Secteur, Téléphone, Site Web, Adresse,
Ville, Code Postal, Email (via le site web de l'entreprise).

## 1. Prérequis
- Python 3.9+ installé
- Google Chrome installé sur ta machine (le script pilote Chrome directement)

## 2. Installation

Ouvre un terminal dans le dossier du projet :

```bash
cd gmaps-scraper-py
pip install -r requirements.txt
playwright install chrome
```

La dernière commande dit à Playwright d'utiliser ton Chrome (rien à télécharger
en plus si Chrome est déjà installé, ça enregistre juste le canal).

## 3. Configuration

- **`config/villes.py`** → ⚠️ à remplir avec ta vraie liste de villes
- **`config/sectors.py`** → déjà rempli avec tous tes secteurs

## 4. Lancer le scraping

```bash
python main.py
```

- Une fenêtre Chrome va s'ouvrir automatiquement — laisse-la ouverte
- Chaque combinaison secteur × ville est traitée l'une après l'autre
- Les résultats sont écrits **au fur et à mesure** dans `output/resultats.csv`
- La progression est sauvegardée dans `progress/done.json` : si tu arrêtes
  (Ctrl+C) et relances plus tard, il reprend là où il s'était arrêté

## 5. Résultat

Fichier `output/resultats.csv` avec les colonnes :

```
Nom Société, Secteur d'Activité, Téléphone, Site Web, Adresse, Ville, Code Postal, Mail
```

## 6. Points importants

- **Volume** : ~44 secteurs × N villes = beaucoup de recherches. Chaque
  combinaison peut remonter jusqu'à 60 fiches (réglable dans `main.py`,
  paramètre `max_results`).
- **Email** : pas toujours trouvable, le champ reste vide si le site n'a pas
  de page contact exploitable ou pas de site web du tout.
- **Anti-blocage** : pauses aléatoires entre chaque recherche. Si Google
  affiche un captcha, ralentis (augmente les `time.sleep`) et évite de lancer
  plusieurs sessions en parallèle.
- **Légal** : le scraping B2B (données d'entreprises publiques) est
  généralement plus toléré que le B2C (données personnelles). Vérifie la
  conformité RGPD / loi 09-08 (Maroc) selon l'usage prévu.

## 7. Structure du projet

```
gmaps-scraper-py/
├── config/
│   ├── sectors.py       # liste des secteurs à scraper
│   └── villes.py        # liste des villes (À REMPLIR)
├── src/
│   ├── scraper.py         # logique de scraping d'une fiche Google Maps
│   ├── email_finder.py    # recherche d'email sur le site de l'entreprise
│   └── csv_writer.py      # écriture incrémentale du CSV
├── output/
│   └── resultats.csv      # résultats (généré à l'exécution)
├── progress/
│   └── done.json          # combinaisons déjà traitées (reprise auto)
├── main.py                 # script principal à lancer
├── requirements.txt
└── README.md
```
