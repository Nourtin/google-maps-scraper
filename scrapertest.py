"""
Script Playwright multi-sites piloté par un fichier YAML.

VERSION SIMPLIFIÉE : ne fait PLUS de recherche (pas de remplissage de
formulaire, pas de clic sur "submit", pas d'extraction de résultats).
Le script se contente d'ouvrir chaque site listé dans config/sites.yml
(en passant par le VPN si activé), d'attendre le chargement, puis de
passer au site suivant.

- Lit config/sites.yml (URLs)
- Ouvre chaque URL dans l'onglet
- Ne lit plus data/noms.csv ni data/villes.csv (plus nécessaires)
- Aucune extraction : output/resultats.csv n'est plus généré

IMPORTANT :
  Le script ouvre TON Chrome principal (avec tes extensions) via son
  profil utilisateur. Ferme TOUTES les fenêtres Chrome (et vérifie dans
  le Gestionnaire des tâches qu'aucun chrome.exe ne tourne encore en
  arrière-plan) avant de lancer ce script.
"""

import asyncio
import json
import platform
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import yaml
from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "sites.yml"

# -------------------------------------------------------------------------
# Chemin du profil Chrome principal
# -------------------------------------------------------------------------
# !!! REMPLIS CES DEUX VARIABLES A LA MAIN AVEC CE QUE TU VOIS DANS
# !!! chrome://version ("Chemin du profil") -- NE TE FIE PAS AU CHEMIN
# !!! AUTO-DETECTE CI-DESSOUS, C'EST UN CHEMIN "PROBABLE" PAR DEFAUT.

def default_chrome_profile_dir() -> str:
    home = Path.home()
    system = platform.system()
    if system == "Windows":
        return str(home / "AppData/Local/Google/Chrome/User Data")
    if system == "Darwin":  # macOS
        return str(home / "Library/Application Support/Google/Chrome")
    return str(home / ".config/google-chrome")

CHROME_USER_DATA_DIR = r"C:\Users\um6p\ChromeAutomation\User Data"
CHROME_PROFILE_DIRECTORY = "Default"

# -------------------------------------------------------------------------
# Délais -- RÈGLE TOUT ICI EN SECONDES, la conversion en ms pour Playwright
# est faite automatiquement juste en dessous.
# -------------------------------------------------------------------------
NAV_TIMEOUT_S = 60              # timeout de navigation
POST_ACTION_WAIT_S = 1
BROWSER_LAUNCH_TIMEOUT_S = 20   # si Chrome ne s'ouvre pas en 20s -> erreur claire
EXT_SW_WAIT_TIMEOUT_S = 15      # attente max du service worker de l'extension
NETWORKIDLE_WAIT_S = 15         # attente max de networkidle APRES un chargement réussi

# --- Conversions en ms pour l'API Playwright (ne pas toucher) ---
NAV_TIMEOUT = NAV_TIMEOUT_S * 1000
POST_ACTION_WAIT = POST_ACTION_WAIT_S * 1000
BROWSER_LAUNCH_TIMEOUT = BROWSER_LAUNCH_TIMEOUT_S * 1000
EXT_SW_WAIT_TIMEOUT_MS = EXT_SW_WAIT_TIMEOUT_S * 1000
NETWORKIDLE_WAIT_MS = NETWORKIDLE_WAIT_S * 1000

# Paramètres pour la logique de connexion VPN robuste (en secondes)
VPN_CONNECT_MAX_ATTEMPTS = 3         # nombre de tentatives de connexion VPN
VPN_STATE_WAIT_S = 10                # attente max de l'état "connected" dans le DOM
VPN_IP_SETTLE_DELAY_S = 5            # délai après "connected" avant de vérifier l'IP réelle

# Erreurs réseau qui indiquent typiquement un tunnel/proxy VPN instable
# (le bouton "connected" et le test d'IP peuvent être bons un instant,
# puis le tunnel du proxy tombe ou n'est pas encore stable).
TUNNEL_ERROR_SNIPPETS = [
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_CONNECTION_FAILED",
    "ERR_CONNECTION_CLOSED",
    "ERR_CONNECTION_RESET",
    "ERR_EMPTY_RESPONSE",
]
GOTO_MAX_ATTEMPTS = 4                # tentatives de page.goto avant d'abandonner un site
GOTO_RETRY_DELAY_S = 4               # délai entre deux tentatives de page.goto
VPN_RECONNECT_AFTER_N_FAILURES = 2   # après ce nb d'échecs de tunnel, on retente une reconnexion VPN


def log(msg):
    print(msg, flush=True)


def load_config():
    log(f"[DEBUG] Lecture config : {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    log(f"[DEBUG] {len(cfg['sites'])} site(s) chargé(s) depuis sites.yml")
    return cfg


VEEPN_EXTENSION_ID = "majdfhpaihoncoakbjgbdhglocklcgno"  # ex: "majdfhpaihoncoakbjgbdhglocklcgno"


async def wait_for_extension_sw(context, timeout=EXT_SW_WAIT_TIMEOUT_MS):
    """
    Attend que le service worker de l'extension VeePN soit bien enregistré
    et actif avant de tenter d'interagir avec le popup.
    """
    start = time.time()
    while (time.time() - start) * 1000 < timeout:
        workers = [sw.url for sw in context.service_workers if VEEPN_EXTENSION_ID in sw.url]
        if workers:
            log(f"[DEBUG] Service worker VeePN actif après {int((time.time() - start) * 1000)}ms : {workers}")
            return workers
        await asyncio.sleep(0.5)
    log(f"[!] Service worker VeePN non détecté après {timeout}ms.")
    return []


async def get_public_ip(context, timeout=8000):
    """Récupère l'IP publique actuelle via une page temporaire."""
    ip_page = await context.new_page()
    try:
        await ip_page.goto("https://api.ipify.org", timeout=timeout, wait_until="domcontentloaded")
        ip = (await ip_page.inner_text("body")).strip()
        return ip
    except Exception as e:
        log(f"[!] Impossible de récupérer l'IP publique : {e}")
        return None
    finally:
        await ip_page.close()


async def activate_veepn(context, page=None, site_url=None):
    """
    Active VeePN via son popup, avec vérification RÉELLE de la connexion :
    on vérifie que l'IP publique change réellement avant/après la connexion.
    Retente automatiquement (jusqu'à VPN_CONNECT_MAX_ATTEMPTS fois).

    Si la connexion réussit ET qu'on nous a passé une page + une URL de site,
    on recharge cette page APRES connexion.
    """
    log("[DEBUG] Attente de l'initialisation du service worker de l'extension...")
    await wait_for_extension_sw(context)
    log(f"[DEBUG] Service workers détectés : {[sw.url for sw in context.service_workers]}")

    ext_id = VEEPN_EXTENSION_ID

    manifest_page = await context.new_page()
    popup_path = None
    try:
        await manifest_page.goto(f"chrome-extension://{ext_id}/manifest.json", wait_until="load", timeout=10000)
        raw = await manifest_page.evaluate("document.body.innerText || document.body.textContent")
        manifest = json.loads(raw)
        popup_path = manifest.get("action", {}).get("default_popup") or \
                     manifest.get("browser_action", {}).get("default_popup")
    except Exception as e:
        log(f"[!] Erreur lecture manifest.json : {type(e).__name__}: {e}")
    finally:
        await manifest_page.close()

    if not popup_path:
        log("[!] Impossible de déterminer le popup depuis le manifest, abandon.")
        return False

    popup_url = f"chrome-extension://{ext_id}/{popup_path}"
    popup_page = await context.new_page()
    connected = False

    try:
        await popup_page.goto(popup_url, timeout=10000)
        await popup_page.wait_for_load_state("networkidle", timeout=10000)
        await popup_page.wait_for_timeout(1000)

        connect_button = "button.connect-button"

        ip_before = await get_public_ip(context)
        log(f"[DEBUG] IP avant connexion VPN : {ip_before}")

        for attempt in range(1, VPN_CONNECT_MAX_ATTEMPTS + 1):
            try:
                await popup_page.click(connect_button, timeout=5000)
                log(f"[DEBUG] (tentative {attempt}/{VPN_CONNECT_MAX_ATTEMPTS}) Clic sur le bouton Connect effectué.")
            except Exception as e:
                log(f"[!] Clic sur Connect impossible (tentative {attempt}) : {e}")
                await popup_page.wait_for_timeout(2000)
                continue

            state_ok = False
            for _ in range(VPN_STATE_WAIT_S * 2):
                class_now = await popup_page.evaluate(
                    "document.querySelector('button.connect-button')?.className || ''"
                )
                if "connect-button--connected" in class_now:
                    state_ok = True
                    log(f"[DEBUG] Nouvel état du bouton : {class_now}")
                    break
                if "connect-button--disconnected" in class_now:
                    log(f"[!] Le bouton est repassé en 'disconnected' : {class_now}")
                    break
                await popup_page.wait_for_timeout(500)

            if not state_ok:
                log(f"[!] État 'connected' non atteint (tentative {attempt}), nouvel essai...")
                continue

            await asyncio.sleep(VPN_IP_SETTLE_DELAY_S)
            ip_after = await get_public_ip(context)
            log(f"[DEBUG] IP après tentative {attempt} : {ip_after}")

            if ip_after and ip_before and ip_after != ip_before:
                connected = True
                log("[DEBUG] VeePN est bien connecté ET l'IP publique a changé (confirmé réellement).")
                break
            else:
                log(f"[!] Bouton 'connected' mais IP inchangée ({ip_after}) -> "
                    f"le tunnel VPN n'est pas encore réellement actif, nouvel essai...")
                await popup_page.wait_for_timeout(2000)

        if not connected:
            log("[!] Échec : le VPN n'a jamais réellement changé l'IP après plusieurs tentatives.")
            await popup_page.screenshot(path=str(BASE_DIR / "debug_veepn_after_click.png"))

    except Exception as e:
        log(f"[!] Impossible d'activer VeePN automatiquement : {e}")
    finally:
        await popup_page.close()

    if connected and page is not None and site_url:
        try:
            try:
                await context.clear_cookies(domain=urlparse(site_url).hostname)
                log(f"[DEBUG] Cookies effacés pour le domaine {urlparse(site_url).hostname}.")
            except Exception as e:
                log(f"[!] Impossible d'effacer les cookies du domaine : {e}")

            log(f"[DEBUG] VPN actif -> rafraîchissement de {site_url} pour que le site détecte le VPN...")
            await page.goto(site_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await page.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            log("[DEBUG] Site rechargé après connexion VPN.")
        except Exception as e:
            log(f"[!] Impossible de rafraîchir la page après connexion VPN : {e}")

    return connected


async def launch_chrome(p):
    """(Re)lance Chrome avec le profil persistant."""
    context = await p.chromium.launch_persistent_context(
        user_data_dir=CHROME_USER_DATA_DIR,
        channel="chrome",
        headless=False,
        args=[f"--profile-directory={CHROME_PROFILE_DIRECTORY}"],
        ignore_default_args=[
            "--disable-extensions",
            "--disable-component-extensions-with-background-pages",
        ],
        timeout=BROWSER_LAUNCH_TIMEOUT,
    )
    page = await context.new_page()
    return context, page


async def wait_profile_unlocked(user_data_dir, max_wait_s=15):
    """Attend que le fichier de verrou du profil Chrome soit libéré."""
    start = time.time()
    while (time.time() - start) < max_wait_s:
        try:
            singleton_lock = Path(user_data_dir) / "SingletonLock"
            if not singleton_lock.exists():
                return True
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return False


def _is_tunnel_error(exc) -> bool:
    err_str = str(exc)
    return any(snippet in err_str for snippet in TUNNEL_ERROR_SNIPPETS)


async def visit_site(context, site_cfg, vpn_page=None):
    """
    Ouvre simplement la page d'accueil du site, SANS recherche :
    pas de remplissage de champ, pas de clic sur "submit", pas
    d'extraction de résultats.

    IMPORTANT : un NOUVEL onglet est ouvert pour chaque tentative de
    navigation (au lieu de réutiliser toujours le même onglet). Chrome
    met en cache les connexions réseau (sockets) par onglet/processus ;
    si une navigation précédente a établi une connexion à travers un
    tunnel VPN pas encore stable, cette connexion cassée peut être
    réutilisée pour les navigations suivantes sur ce même onglet, même
    une fois le VPN pleinement actif. Un onglet neuf force Chrome à
    créer de nouvelles connexions.

    Si la navigation échoue à cause d'une erreur de tunnel/proxy VPN
    (ERR_TUNNEL_CONNECTION_FAILED et similaires), on retente plusieurs
    fois (avec un onglet neuf à chaque fois) avec un court délai. Après
    VPN_RECONNECT_AFTER_N_FAILURES échecs consécutifs de ce type, on
    tente une reconnexion VPN (via `vpn_page`, l'onglet dédié au popup
    VeePN) avant de continuer les tentatives.

    Retourne l'onglet ayant réussi la navigation (laissé ouvert), pour
    inspection éventuelle ; l'appelant est responsable de le fermer.
    """
    site_name = site_cfg["name"]
    site_url = site_cfg["url"]

    log(f"  -> Site: {site_name}")

    tunnel_failures = 0
    last_exc = None
    page = None

    for attempt in range(1, GOTO_MAX_ATTEMPTS + 1):
        # Onglet neuf à chaque tentative pour éviter de réutiliser une
        # connexion/tunnel potentiellement cassée d'une tentative précédente.
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
        page = await context.new_page()

        try:
            log(f"     Navigation vers {site_url} (tentative {attempt}/{GOTO_MAX_ATTEMPTS}, "
                f"nouvel onglet) ...")
            await page.goto(site_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        except Exception as e:
            last_exc = e
            if _is_tunnel_error(e):
                tunnel_failures += 1
                log(f"     [!] Erreur de tunnel/proxy VPN (tentative {attempt}) : {e}")

                if vpn_page is not None and tunnel_failures >= VPN_RECONNECT_AFTER_N_FAILURES:
                    log("     [!] Plusieurs échecs de tunnel consécutifs -> "
                        "tentative de reconnexion VPN avant de continuer...")
                    try:
                        await activate_veepn(context, page=vpn_page, site_url=site_url)
                    except Exception as vpn_e:
                        log(f"     [!] Échec de la reconnexion VPN : {vpn_e}")
                    tunnel_failures = 0

                if attempt < GOTO_MAX_ATTEMPTS:
                    await asyncio.sleep(GOTO_RETRY_DELAY_S)
                    continue
                # dernière tentative épuisée -> on sort de la boucle vers l'échec final
                break
            else:
                # Erreur non liée au tunnel -> on ne s'acharne pas, on relance
                # l'exception pour que l'appelant gère (ex: navigateur fermé).
                raise

        # `domcontentloaded` a réussi : la navigation a fonctionné, ce n'est
        # donc PAS une erreur de tunnel/VPN. On attend networkidle en best
        # effort seulement -- beaucoup de sites modernes (chat, ads, tuiles
        # de carte, websockets, polling) ne deviennent jamais "idle" et ce
        # timeout n'a rien à voir avec le VPN. Avant, ce timeout n'était pas
        # reconnu comme erreur de tunnel et remontait tel quel, faisant
        # échouer tout le site alors que la page était déjà chargée.
        try:
            await page.wait_for_load_state("networkidle", timeout=NETWORKIDLE_WAIT_MS)
        except PWTimeoutError:
            log("     [DEBUG] networkidle non atteint (normal sur pas mal de sites), on continue quand même.")

        await page.wait_for_timeout(POST_ACTION_WAIT)
        # Force cet onglet au premier plan : sans ça, Chrome ne bascule
        # pas forcément dessus visuellement, alors que la navigation a
        # bien réussi côté script (d'où l'impression que "la page ne
        # s'ouvre pas" alors qu'elle est en fait chargée dans un onglet
        # que tu ne regardais pas).
        await page.bring_to_front()
        log(f"     Page chargée : {page.url}")
        return page

    # Toutes les tentatives ont échoué avec une erreur de tunnel/proxy.
    log(f"     [ERREUR] Abandon de {site_name} après {GOTO_MAX_ATTEMPTS} tentatives "
        f"(dernière erreur : {last_exc}).")
    try:
        if page is not None:
            await page.close()
    except Exception:
        pass
    raise last_exc


async def main():
    log("[DEBUG] Démarrage du script...")
    log(f"[DEBUG] CHROME_USER_DATA_DIR = {CHROME_USER_DATA_DIR}")
    log(f"[DEBUG] CHROME_PROFILE_DIRECTORY = {CHROME_PROFILE_DIRECTORY}")

    if not Path(CHROME_USER_DATA_DIR).exists():
        log(f"[ERREUR] Le dossier {CHROME_USER_DATA_DIR} n'existe pas.")
        log("Corrige CHROME_USER_DATA_DIR dans scraper.py avec le chemin exact de chrome://version.")
        sys.exit(1)

    config = load_config()

    log("[DEBUG] Tentative d'ouverture de Chrome (launch_persistent_context)...")
    async with async_playwright() as p:
        try:
            context, page = await launch_chrome(p)
        except Exception as e:
            log("[ERREUR] Impossible d'ouvrir Chrome avec ce profil.")
            log("-> Vérifie qu'AUCUN processus chrome.exe ne tourne (Gestionnaire des tâches).")
            log("-> Vérifie CHROME_USER_DATA_DIR / CHROME_PROFILE_DIRECTORY dans scraper.py.")
            log(f"Erreur détaillée : {e}")
            sys.exit(1)

        log("[DEBUG] Chrome ouvert avec succès.")
        log("[DEBUG] Nouvel onglet créé (dédié à la gestion du VPN).")

        real_ip = await get_public_ip(context)
        log(f"[DEBUG] IP réelle (sans VPN) de référence : {real_ip}")

        first_site_url = config["sites"][0]["url"] if config.get("sites") else None
        # `page` reste l'onglet utilisé pour piloter le popup VeePN
        # (activation/reconnexion). Chaque site sera visité dans son
        # PROPRE onglet neuf via visit_site(), pour éviter de réutiliser
        # une connexion/tunnel potentiellement cassée.
        vpn_ok = await activate_veepn(context, page=page, site_url=first_site_url)
        if not vpn_ok:
            log("[!] ATTENTION : le VPN n'a pas pu être confirmé actif (IP inchangée).")

        log(f"[DEBUG] Nombre d'onglets déjà ouverts : {len(context.pages)}")
        log("[DEBUG] Début des visites de sites (sans recherche)...")

        for site in config["sites"]:
            site_page = None
            try:
                site_page = await visit_site(context, site, vpn_page=page)
            except Exception as e:
                err_str = str(e)
                if "has been closed" in err_str or "Target page" in err_str:
                    log(f"  [!] Navigateur fermé de manière inattendue pendant {site['name']}. "
                        f"Relance et nouvelle tentative...")
                    try:
                        await wait_profile_unlocked(CHROME_USER_DATA_DIR)
                        await asyncio.sleep(3)
                        context, page = await launch_chrome(p)
                        await wait_for_extension_sw(context)
                        await asyncio.sleep(2)
                        retry_ip = await get_public_ip(context)
                        if not retry_ip or retry_ip == real_ip:
                            log("  [!] VPN déconnecté après relance imprévue -> reconnexion...")
                            await activate_veepn(context, page=page, site_url=first_site_url)
                        site_page = await visit_site(context, site, vpn_page=page)
                    except Exception as e2:
                        log(f"  [ERREUR] Échec même après relance : {site['name']}: {e2}")
                elif _is_tunnel_error(e):
                    log(f"  [ERREUR] Tunnel VPN instable, {site['name']} abandonné après retries : {e}")
                else:
                    log(f"  [ERREUR] {site['name']}: {e}")
            # Note : on NE ferme PLUS l'onglet du site après la visite.
            # La page reste ouverte pour que tu puisses la consulter
            # manuellement une fois le script terminé.

        log("\nTerminé : tous les sites ont été visités (aucune recherche effectuée, "
            "aucun fichier de résultats généré).")
        log("[DEBUG] Le navigateur va rester ouvert tant que tu n'as pas validé ci-dessous.")
        log(f"[DEBUG] Onglets actuellement ouverts ({len(context.pages)}) :")
        for i, p_open in enumerate(context.pages):
            log(f"         [{i}] {p_open.url}")

        # IMPORTANT : même sans appeler context.close(), le fait de sortir
        # du bloc "async with async_playwright() as p:" arrête le driver
        # Playwright, ce qui termine aussi le processus Chrome qu'il a
        # lancé (comportement par défaut avec launch_persistent_context).
        # On bloque donc ici explicitement, pour laisser la page ouverte
        # et consultable jusqu'à ce que l'utilisateur confirme.
        await asyncio.get_event_loop().run_in_executor(
            None, input, "\nAppuie sur Entrée ici pour fermer le navigateur et quitter le script...\n"
        )

        try:
            await context.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
