import datetime
import json
import os
import time
from fpdf import FPDF
from PIL import Image
import streamlit as st
import streamlit.components.v1 as components
import extra_streamlit_components as stx
from supabase import Client, create_client
import projets_config

# Importation sécurisée du gestionnaire Hors-Ligne SQLite
try:
  from offline_manager import (
      get_pending_count,
      init_offline_db,
      insert_safe,
      sync_data_to_supabase,
  )

  init_offline_db()
  OFFLINE_SUPPORT = True
except ImportError:
  OFFLINE_SUPPORT = False

# ==========================================
# 1. CONFIGURATION DE LA PAGE & INJECTION PWA
# ==========================================
st.set_page_config(
    page_title="LPEE - CTR-CSB",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==========================================
# 1bis. CAPTURE DES PARAMÈTRES QR CODE (ex: ?rec=...&beton_id=...)
# ==========================================
# C'est ICI qu'il faut lire l'URL, et pas dans views/suivi_controle_beton.py :
# ce module est importé (pas exécuté directement), donc son bloc
# "if __name__ == '__main__':" ne s'exécute jamais dans l'app déployée.
_query_params = st.query_params
_qr_rec = _query_params.get("rec") or _query_params.get("num_reception")
_qr_bid = _query_params.get("beton_id") or _query_params.get("id")
_qr_ep = _query_params.get("ep")

if _qr_rec or _qr_bid:
    if _qr_rec:
        st.session_state["pending_qr_rec"] = str(_qr_rec).strip()
    if _qr_bid:
        st.session_state["pending_qr_bid"] = str(_qr_bid).strip()
    if _qr_ep:
        st.session_state["pending_qr_ep"] = str(_qr_ep).strip()

    # (Ré)armer la redirection automatique vers la page "Suivi Contrôle Béton"
    st.session_state["qr_page_applied"] = False

    # Nettoyer l'URL : sans ça, ce bloc s'exécuterait à nouveau à CHAQUE clic
    # / rerun et forcerait la page à chaque interaction (ce qui empêcherait
    # toute navigation manuelle une fois arrivé sur la bonne page).
    st.query_params.clear()

# ==========================================
# 1ter. "SE SOUVENIR DE MOI" — COOKIE VIA COMPOSANT (rapide, sans rechargement)
# ==========================================
# Permet de rester connecté sur le même appareil/navigateur, y compris après
# un scan QR Code qui ouvre une nouvelle session Streamlit (donc un
# session_state vide) : sans cookie, il faudrait ressaisir le mot de passe
# à CHAQUE scan.
#
# NOTE : une version précédente lisait/écrivait le cookie via un
# rechargement complet de la page (fiable, mais lent : ~30-40s à chaque
# scan QR). On revient donc au composant (rapide, pas de rechargement),
# avec deux corrections de timing pour fiabiliser sur Safari/iOS :
#  1) un premier passage "à vide" forcé pour laisser le composant le temps
#     de récupérer les cookies déjà présents avant toute vérification,
#  2) une courte pause après l'écriture d'un nouveau cookie, avant de
#     recharger la vue, pour laisser le temps au navigateur de l'enregistrer.
REMEMBER_SECRET_KEY = os.environ.get(
    "REMEMBER_SECRET_KEY", "lpee_ctr_csb_remember_me_2026_a_changer"
)
REMEMBER_SESSION_DUREE = datetime.timedelta(hours=4)
REMEMBER_COOKIE_NAME = "remember_data"


def _generer_jeton_souvenir(username, role, can_edit, projets_autorises, issued_at_iso):
    """Jeton signé auto-suffisant (indépendant de la base utilisateurs),
    pour fonctionner avec les 3 chemins de connexion possibles (compte
    nommé, mot de passe maître admin2026, mot de passe maître ctr2026).
    L'horodatage de connexion (issued_at_iso) est inclus dans la signature
    afin de pouvoir vérifier côté serveur que la session ne dépasse pas
    REMEMBER_SESSION_DUREE, indépendamment de l'expiration du cookie
    navigateur (qu'un changement d'horloge sur l'appareil pourrait fausser).
    Les projets autorisés sont inclus dans la signature : sans ça, on
    pourrait modifier le cookie côté navigateur pour s'octroyer l'accès à
    un autre projet sans que la signature ne s'invalide."""
    import hashlib
    import hmac as hmac_lib
    projets_str = ",".join(sorted(projets_autorises or []))
    payload = f"{username}:{role}:{bool(can_edit)}:{projets_str}:{issued_at_iso}"
    return hmac_lib.new(
        REMEMBER_SECRET_KEY.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


cookie_manager = stx.CookieManager(key="lpee_ctr_csb_cookie_manager")

# Premier passage forcé : sur Safari/iOS notamment, le composant (chargé
# dans un iframe) a besoin d'un premier aller-retour avant de renvoyer les
# cookies réellement présents. Sans ce passage, la vérification "se
# souvenir de moi" plus bas risquerait de s'exécuter avec une valeur encore
# vide et donc d'afficher l'écran de connexion à tort.
if "_cookies_bootstrap_ok" not in st.session_state:
    st.session_state["_cookies_bootstrap_ok"] = True
    st.rerun()



# Injection PWA dans le HEAD du document principal
pwa_code = """
<script>
const parentDoc = window.parent.document;

if (!parentDoc.querySelector('link[rel="manifest"]')) {
    const manifestLink = parentDoc.createElement('link');
    manifestLink.rel = 'manifest';
    manifestLink.href = '/manifest.json';
    parentDoc.head.appendChild(manifestLink);
}

if (!parentDoc.querySelector('meta[name="theme-color"]')) {
    const metaTheme = parentDoc.createElement('meta');
    metaTheme.name = 'theme-color';
    metaTheme.content = '#0066cc';
    parentDoc.head.appendChild(metaTheme);
}

if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js')
        .then((reg) => console.log('Service Worker PWA enregistré !', reg))
        .catch((err) => console.error('Erreur Service Worker PWA :', err));
}
</script>
"""
components.html(pwa_code, height=0, width=0)


# ==========================================
# 2. MODULE DE GÉNÉRATION PDF (FPDF2)
# ==========================================
class LPEEPDFReport(FPDF):

  def header(self):
    self.set_font("Helvetica", "B", 14)
    self.cell(
        0,
        8,
        "LABORATOIRE PUBLIC D'ESSAIS ET D'ÉTUDES - LPEE",
        border=False,
        new_x="LMARGIN",
        new_y="NEXT",
        align="C",
    )
    self.set_font("Helvetica", "B", 11)
    self.cell(
        0,
        6,
        "CENTRE TECHNIQUE RÉGIONAL - LGV CASA SUD (CTR-CSB)",
        border=False,
        new_x="LMARGIN",
        new_y="NEXT",
        align="C",
    )
    self.set_font("Helvetica", "I", 9)
    self.cell(
        0,
        5,
        "Rapport Officiel de Contrôle Qualité",
        border=False,
        new_x="LMARGIN",
        new_y="NEXT",
        align="C",
    )
    self.ln(3)
    self.line(10, 28, 200, 28)
    self.ln(5)

  def footer(self):
    self.set_y(-15)
    self.set_font("Helvetica", "I", 8)
    self.cell(
        0, 10, f"Page {self.page_no()}/{{nb}}", border=False, align="C"
    )


def generate_pdf_report(
    title: str, data_rows: list, headers: list = None
) -> bytes:
  """Génère un document PDF binaire prêt pour st.download_button."""
  pdf = LPEEPDFReport()
  pdf.alias_nb_pages()
  pdf.add_page()

  pdf.set_font("Helvetica", "B", 12)
  pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
  pdf.set_font("Helvetica", "", 9)
  pdf.cell(
      0,
      6,
      f"Édité le : {datetime.date.today().strftime('%d/%m/%Y')}",
      new_x="LMARGIN",
      new_y="NEXT",
  )
  pdf.ln(4)

  if headers:
    pdf.set_fill_color(220, 230, 242)
    pdf.set_font("Helvetica", "B", 9)
    col_width = 190 / len(headers)
    for h in headers:
      pdf.cell(col_width, 7, str(h), 1, 0, "C", fill=True)
    pdf.ln()

  pdf.set_font("Helvetica", "", 9)
  for row in data_rows:
    col_width = 190 / (len(headers) if headers else len(row))
    for item in row:
      pdf.cell(col_width, 6, str(item), 1, 0, "C")
    pdf.ln()

  return bytes(pdf.output())


# ==========================================
# 3. CONNEXION SUPABASE & GESTION BDD USERS
# ==========================================
try:
  SUPABASE_URL = st.secrets.get(
      "SUPABASE_URL", "https://votre-projet.supabase.co"
  )
  SUPABASE_KEY = st.secrets.get(
      "SUPABASE_KEY", "sb_publishable_m8g5mocsCDgk3JpS1lpuCQ_3wOPyet1"
  )
  # Code partagé exigé par la politique RLS sur suivi_betonnage (voir le SQL
  # fourni pour la page hors-ligne). L'app principale doit envoyer le même
  # en-tête que offline_betonnage.html, sinon ses propres insertions seraient
  # bloquées par cette même règle de sécurité.
  CODE_ACCES_TERRAIN = st.secrets.get("CODE_ACCES_TERRAIN", "CHANGEZ_MOI_2026")

  # Création du client SANS argument supplémentaire (comme avant) : c'est le
  # passage d'un ClientOptions à create_client() qui faisait planter la
  # connexion selon les versions de la librairie supabase-py.
  supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

  # Ajout de l'en-tête APRÈS coup, de façon tolérante : si la structure
  # interne de la librairie diffère selon la version installée, l'app
  # continue de fonctionner normalement (juste sans cet en-tête ajouté).
  try:
    supabase.postgrest.session.headers.update(
        {"x-code-acces-terrain": CODE_ACCES_TERRAIN}
    )
  except Exception:
    try:
      supabase.postgrest._client.headers.update(
          {"x-code-acces-terrain": CODE_ACCES_TERRAIN}
      )
    except Exception:
      pass
except Exception:
  supabase = None

DEFAULT_USERS = {
    "BAALLAL": {"password": "arwa2020", "role": "admin", "can_edit": True, "projets_autorises": ["LGV_CASA_SUD"]},
    "AMINA": {"password": "amina2026", "role": "laboratoire", "can_edit": True, "projets_autorises": ["LGV_CASA_SUD"]},
    "HANINE": {
        "password": "hanine2026",
        "role": "laboratoire",
        "can_edit": False,
        "projets_autorises": ["LGV_CASA_SUD"],
    },
    "IKKEN": {"password": "ikken2026", "role": "laboratoire", "can_edit": False, "projets_autorises": ["LGV_CASA_SUD"]},
    "HAMDANI": {
        "password": "hamdani2026",
        "role": "laboratoire",
        "can_edit": False,
        "projets_autorises": ["LGV_CASA_SUD"],
    },
    "ADAM": {
        "password": "ctr2026",
        "role": "restricted_betonnage",
        "can_edit": False,
        "projets_autorises": ["LGV_CASA_SUD"],
    },
    "LAHCEN": {
        "password": "ctr2026",
        "role": "restricted_betonnage",
        "can_edit": False,
        "projets_autorises": ["LGV_CASA_SUD"],
    },
    "ELIDRISSI": {
        "password": "ctr2026",
        "role": "restricted_betonnage",
        "can_edit": False,
        "projets_autorises": ["LGV_CASA_SUD"],
    },
}


def load_users():
  """Charge en temps réel les utilisateurs depuis Supabase."""
  users = DEFAULT_USERS.copy()
  if supabase:
    try:
      res = supabase.table("app_users").select("*").execute()
      if res.data:
        for row in res.data:
          projets_bruts = row.get("projets_autorises") or ""
          users[row["username"]] = {
              "password": row["password"],
              "role": row["role"],
              "can_edit": row.get("can_edit", False),
              "projets_autorises": [
                  p.strip() for p in projets_bruts.split(",") if p.strip()
              ],
          }
    except Exception:
      pass
  return users


def save_user_db(username, password, role, can_edit, projets_autorises=None):
  """Enregistre ou met à jour un utilisateur dans Supabase."""
  if not supabase:
    return False, "Client Supabase non configuré."
  try:
    supabase.table("app_users").upsert({
        "username": username,
        "password": password,
        "role": role,
        "can_edit": can_edit,
        "projets_autorises": ",".join(projets_autorises or []),
    }).execute()
    return True, None
  except Exception as e:
    return False, str(e)


def delete_user_db(username):
  """Supprime un utilisateur de Supabase."""
  if not supabase:
    return False, "Client Supabase non configuré."
  try:
    supabase.table("app_users").delete().eq("username", username).execute()
    return True, None
  except Exception as e:
    return False, str(e)


# Chargement paresseux : on ne fait l'appel réseau Supabase que lorsque
# c'est réellement nécessaire (connexion manuelle ou auto-connexion réussie
# via cookie, plus bas). Le faire ici de façon systématique coûtait un
# aller-retour réseau inutile sur CHAQUE nouveau scan QR, y compris sur le
# passage "jetable" qui précède la vérification du cookie.
st.session_state.setdefault("users_db", {})

if "user" not in st.session_state:
  st.session_state["user"] = None
if "role" not in st.session_state:
  st.session_state["role"] = None
if "can_edit" not in st.session_state:
  st.session_state["can_edit"] = False

# ==========================================
# 3bis. AUTO-CONNEXION VIA COOKIE "SE SOUVENIR DE MOI"
# ==========================================
if st.session_state["user"] is None:
  _cookie_brut = cookie_manager.get(REMEMBER_COOKIE_NAME)
  if _cookie_brut:
    try:
      payload = json.loads(_cookie_brut)
      remembered_user = payload.get("u")
      remembered_role = payload.get("r")
      remembered_can_edit = bool(payload.get("e"))
      remembered_projets = payload.get("p") or []
      remembered_issued_at = payload.get("t")
      remembered_token = payload.get("k")

      jeton_valide = bool(remembered_token) and _generer_jeton_souvenir(
          remembered_user, remembered_role, remembered_can_edit,
          remembered_projets, remembered_issued_at
      ) == remembered_token
      session_expiree = True
      if jeton_valide:
        issued_at_dt = datetime.datetime.fromisoformat(remembered_issued_at)
        session_expiree = (datetime.datetime.utcnow() - issued_at_dt) > REMEMBER_SESSION_DUREE

      if jeton_valide and not session_expiree:
        st.session_state["user"] = {
            "username": remembered_user,
            "role": remembered_role,
            "can_edit": remembered_can_edit,
            "projets_autorises": remembered_projets,
        }
        st.session_state["role"] = remembered_role
        st.session_state["can_edit"] = remembered_can_edit
        st.session_state["users_db"] = load_users()
        # Le cookie peut dater de plusieurs heures : on reprend rôle et
        # projets autorisés depuis la base (nouveau chantier créé, accès
        # retiré...) pour qu'ils soient toujours à jour.
        _rec = st.session_state["users_db"].get(remembered_user)
        if _rec:
          st.session_state["user"]["role"] = _rec["role"]
          st.session_state["user"]["projets_autorises"] = _rec.get("projets_autorises", [])
          st.session_state["role"] = _rec["role"]
    except (ValueError, TypeError, AttributeError, KeyError):
      pass

# ==========================================
# 4. ÉCRAN DE CONNEXION
# ==========================================
if st.session_state["user"] is None:
  col1, col2, col3 = st.columns([1, 2, 1])
  with col2:
    st.markdown("<br><br>", unsafe_allow_html=True)
    st.title("🔐 Accès Restreint - LPEE")
    st.caption("Veuillez saisir vos identifiants pour accéder à la plateforme.")

    image_login_path = os.path.join(os.path.dirname(__file__), "image.page d'accueil.jpg")
    if os.path.exists(image_login_path):
      try:
        img_login = Image.open(image_login_path).convert("RGB")
        st.image(img_login, use_container_width=True)
      except Exception as e:
        st.error(f"Erreur lors de la lecture de l'image : {e}")

    if st.session_state.get("pending_qr_rec") or st.session_state.get("pending_qr_bid"):
      st.info(
          "🎯 **Scan QR Code détecté !** Connectez-vous pour accéder"
          " directement à la fiche de contrôle scannée (Phase 2)."
      )

    with st.form("login_form", clear_on_submit=False):
      username_input = st.text_input("Nom d'utilisateur").strip().upper()
      password_input = st.text_input("Mot de passe", type="password")
      se_souvenir = st.checkbox(
          "🔒 Remember me (4h)",
          value=True,
      )
      submit_btn = st.form_submit_button(
          "Se connecter", use_container_width=True, type="primary"
      )

      def _connecter_et_memoriser(username, role, can_edit, projets_autorises):
        st.session_state["user"] = {
            "username": username, "role": role, "can_edit": can_edit,
            "projets_autorises": projets_autorises,
        }
        st.session_state["role"] = role
        st.session_state["can_edit"] = can_edit
        if se_souvenir:
          issued_at_iso = datetime.datetime.utcnow().isoformat()
          token = _generer_jeton_souvenir(
              username, role, can_edit, projets_autorises, issued_at_iso
          )
          payload_json = json.dumps({
              "u": username, "r": role, "e": bool(can_edit),
              "p": projets_autorises, "t": issued_at_iso, "k": token,
          })
          expiration = datetime.datetime.now() + REMEMBER_SESSION_DUREE
          cookie_manager.set(
              REMEMBER_COOKIE_NAME, payload_json,
              key="set_remember_data", expires_at=expiration,
          )
          # Laisser le temps au navigateur (Safari/iOS en particulier) de
          # réellement écrire le cookie avant que le rerun ci-dessous ne
          # démonte le composant qui le gère.
          time.sleep(0.4)
        st.rerun()


      if submit_btn:
        fresh_users = load_users()
        st.session_state["users_db"] = fresh_users

        if (
            username_input in fresh_users
            and fresh_users[username_input]["password"] == password_input
        ):
          user_role = fresh_users[username_input]["role"]
          can_edit = fresh_users[username_input]["can_edit"]
          user_projets = fresh_users[username_input].get("projets_autorises", [])
          _connecter_et_memoriser(username_input, user_role, can_edit, user_projets)
        elif password_input == "admin2026":
          username = username_input if username_input else "ADMIN"
          # L'admin voit tous les projets (cf. projets_config.liste_projets_utilisateur) :
          # la liste explicite n'est donc pas nécessaire ici, mais on la
          # renseigne quand même pour la cohérence du jeton signé.
          _connecter_et_memoriser(username, "admin", True, list(projets_config.charger_projets(supabase).keys()))
        elif password_input == "ctr2026":
          username = username_input if username_input else "USER"
          # Mot de passe "maître" générique historique : par prudence, il ne
          # donne accès qu'au projet d'origine, jamais automatiquement aux
          # nouveaux projets (qui nécessitent un compte nommé explicitement
          # autorisé, via Gestion Utilisateurs).
          _connecter_et_memoriser(username, "user", False, [projets_config.PROJET_PAR_DEFAUT])
        else:
          st.error("❌ Nom d'utilisateur ou mot de passe incorrect.")
  st.stop()

# Registre des chantiers (table Supabase `projets`), chargé une fois par session
projets_config.charger_projets(supabase)

# Synchronisation du statut d'édition
current_username = st.session_state["user"]["username"]
if current_username in st.session_state["users_db"]:
  st.session_state["can_edit"] = st.session_state["users_db"][
      current_username
  ]["can_edit"]
  st.session_state["user"]["can_edit"] = st.session_state["can_edit"]

# ==========================================
# 5. CODE PRINCIPAL (Utilisateur connecté)
# ==========================================
if (
    st.session_state.get("role") == "user"
    or current_username in ["IKKEN", "HAMDANI"]
):
  st.markdown(
      """
        <style>
        .stDownloadButton { display: none !important; }
        </style>
        """,
      unsafe_allow_html=True,
  )

try:
  from views import (
      historique_pvs,
      suivi_Betonnage,
      suivi_controle_beton,
      synthese_Beton,
  )
except ImportError as e:
  st.error(f"❌ Erreur lors de l'importation des vues : {e}")
  st.stop()

# Menu latéral (Sidebar)
with st.sidebar:
  st.title("LPEE - CTR-CSB")
  current_role = st.session_state["role"]

  st.markdown(f"👤 **{current_username}**")

  projets_config.afficher_selecteur_projet(st.session_state["user"])

  if current_role == "responsable_chantier":
    st.info("Rôle : **RESPONSABLE DE CHANTIER**")
    st.markdown("---")
    available_pages = [
        "Accueil",
        "Chantiers",
        "Suivi Contrôle Béton",
        "Historique Complet & PVs",
        "Suivi de Bétonnage",
        "Synthèse Béton",
    ]
  elif current_role in ["laboratoire", "technicien"]:
    if current_username == "HANINE":
      st.info("Rôle : **RESPONSABLE DE DOSSIER**")
    elif current_username == "AMINA":
      st.info("Rôle : **TECHNICIENNE LABORATOIRE (Saisie/Modification)**")
    else:
      st.info("Rôle : **TECHNICIEN LABORATOIRE**")
    st.markdown("---")
    available_pages = [
        "Accueil",
        "Suivi Contrôle Béton",
        "Historique Complet & PVs",
        "Suivi de Bétonnage",
        "Synthèse Béton",
    ]
  elif current_role == "restricted_betonnage":
    st.info("Rôle : **OPÉRATEUR BÉTONNAGE**")
    st.markdown("---")
    available_pages = ["Suivi de Bétonnage"]
  elif current_role == "admin":
    st.info("Rôle : **ADMINISTRATEUR**")
    st.markdown("---")
    available_pages = [
        "Accueil",
        "Chantiers",
        "Gestion Utilisateurs",
        "Suivi de Bétonnage",
        "Suivi Contrôle Béton",
        "Historique Complet & PVs",
        "Synthèse Béton",
    ]
  elif current_role == "user":
    st.info("Rôle : **CONSULTATION (LECTURE SEULE)**")
    st.markdown("---")
    available_pages = [
        "Accueil",
        "Synthèse Béton",
        "Historique Complet & PVs",
    ]
  else:
    st.info(f"Rôle : **{current_role.upper()}**")
    st.markdown("---")
    available_pages = [
        "Accueil",
        "Synthèse Béton",
        "Historique Complet & PVs",
    ]

  # --- MODULE SYNCHRONISATION HORS LIGNE ---
  if OFFLINE_SUPPORT:
    st.markdown("---")
    pending_count = get_pending_count()
    if pending_count > 0:
      st.warning(f"📦 **{pending_count} fiche(s) en attente** (Hors Ligne)")
      if st.button(
          "🔄 Synchroniser vers Supabase",
          type="primary",
          use_container_width=True,
      ):
        if supabase:
          with st.spinner("Synchronisation des données en cours..."):
            synced_num, errors = sync_data_to_supabase(supabase)
            if synced_num > 0:
              st.success(
                  f"✅ {synced_num} fiche(s) envoyée(s) sur Supabase !"
              )
            if errors:
              for err in errors:
                st.error(err)
            st.rerun()
        else:
          st.error(
              "❌ Pas de connexion Internet détectée pour la synchronisation."
          )
    else:
      st.caption("🟢 Synchronisation : Données à jour.")

  st.markdown("---")

  # --- Redirection automatique vers "Suivi Contrôle Béton" (scan QR) ---
  st.session_state.setdefault("page_widget_seed", 0)
  st.session_state.setdefault("selected_page", None)

  qr_en_attente = bool(
      st.session_state.get("pending_qr_rec") or st.session_state.get("pending_qr_bid")
  )
  forcer_page_qr = qr_en_attente and not st.session_state.get("qr_page_applied", False)
  if forcer_page_qr:
    if "Suivi Contrôle Béton" in available_pages:
      st.session_state["selected_page"] = "Suivi Contrôle Béton"
      # Nouvelle clé => Streamlit traite le widget comme neuf et applique
      # obligatoirement l'index demandé (contrairement à un simple
      # pré-remplissage de session_state sur une clé déjà utilisée, qui peut
      # être ignoré si le widget a déjà un état côté navigateur).
      st.session_state["page_widget_seed"] += 1
    else:
      st.warning(
          "⚠️ Le scan QR pointe vers 'Suivi Contrôle Béton', mais votre rôle"
          " n'a pas accès à cette page."
      )
    st.session_state["qr_page_applied"] = True

  page_par_defaut = st.session_state.get("selected_page")
  if page_par_defaut not in available_pages:
    page_par_defaut = available_pages[0]
  index_par_defaut = available_pages.index(page_par_defaut)

  page = st.radio(
      "Menu Principal",
      available_pages,
      index=index_par_defaut,
      key=f"menu_radio_{st.session_state['page_widget_seed']}",
  )
  st.session_state["selected_page"] = page
  st.markdown("---")

  with st.expander("🔑 Changer mon mot de passe"):
    with st.form("change_pwd_form", clear_on_submit=True):
      old_pwd = st.text_input("Ancien mot de passe", type="password")
      new_pwd = st.text_input("Nouveau mot de passe", type="password")
      confirm_pwd = st.text_input("Confirmer le mot de passe", type="password")
      submit_pwd = st.form_submit_button(
          "Mettre à jour", use_container_width=True
      )

      if submit_pwd:
        user_record = st.session_state["users_db"].get(current_username)

        if user_record and old_pwd != user_record["password"]:
          st.error("❌ L'ancien mot de passe est incorrect.")
        elif new_pwd == "":
          st.warning("⚠️ Le nouveau mot de passe ne peut pas être vide.")
        elif new_pwd != confirm_pwd:
          st.error("❌ Les nouveaux mots de passe ne correspondent pas.")
        else:
          success, err = save_user_db(
              current_username,
              new_pwd,
              user_record["role"],
              user_record["can_edit"],
              user_record.get("projets_autorises", []),
          )
          if success:
            st.session_state["users_db"][current_username]["password"] = new_pwd
            st.success(
                "✅ Mot de passe modifié et synchronisé sur le serveur !"
            )
          else:
            st.error(f"❌ Erreur Supabase : {err}")

  st.markdown("---")
  if st.button("🚪 Déconnexion", use_container_width=True):
    st.session_state["user"] = None
    st.session_state["role"] = None
    st.session_state["can_edit"] = False
    # NOTE : le cookie "se souvenir de moi" n'est PAS supprimé ici. Tant que
    # la fenêtre de 4h (REMEMBER_SESSION_DUREE) n'est pas écoulée, revenir
    # sur l'application reconnectera automatiquement sans redemander le mot
    # de passe. Passé les 4h, le cookie expire et le mot de passe est requis.
    st.rerun()


def render_view(module, supabase_client):
  try:
    module.show(supabase_client, can_edit=st.session_state["can_edit"])
  except TypeError:
    try:
      module.show(supabase_client, user=st.session_state["user"])
    except TypeError:
      module.show(supabase_client)


# ==========================================
# 6. ROUTAGE DES VUES
# ==========================================
if page == "Accueil":
  _nom_proj = projets_config.nom_projet(projets_config.projet_actif(st.session_state["user"]))
  st.title(f"🚄 Accueil - {_nom_proj}")
  st.markdown("### Plateforme de Suivi et Contrôle Qualité - LPEE")

  st.markdown("---")
  col1, col2, col3 = st.columns([1, 2, 1])
  with col2:
    image_path = os.path.join(os.path.dirname(__file__), "al_boraq.jpg.jpg")
    if not os.path.exists(image_path):
      image_path = os.path.join(os.path.dirname(__file__), "al_boraq.jpg")

    if os.path.exists(image_path):
      try:
        img = Image.open(image_path).convert("RGB")
        st.image(
            img,
            caption="Al Boraq - Ligne à Grande Vitesse",
            use_container_width=True,
        )
      except Exception as e:
        st.error(f"Erreur lors de la lecture de l'image : {e}")
    else:
      st.warning("⚠️ L'image 'al_boraq.jpg' est introuvable à la racine.")

  st.markdown("---")
  st.markdown(f"""
    Bienvenue sur l'application centralisée de gestion des contrôles qualité pour le projet **{_nom_proj}**.

    Utilisez le menu de navigation latéral pour accéder aux différents modules de consultation et de suivi.
    """)

elif page == "Gestion Utilisateurs" and current_role == "admin":
  st.title("👥 Gestion des Utilisateurs & Mots de Passe")
  st.caption(
      "Consultez, ajoutez, modifiez et supprimez des utilisateurs de la"
      " plateforme (sauvegarde permanente Supabase)."
  )

  ROLES_LIST = ["laboratoire", "restricted_betonnage", "responsable_chantier", "admin", "user"]
  _registre = projets_config.get_projets()
  PROJETS_LIST = list(_registre.keys())
  PROJETS_LABELS = {
      p: f"{_registre[p]['nom']} ({_registre[p]['client']})" for p in PROJETS_LIST
  }

  col_add, col_edit, col_del = st.columns(3)

  with col_add:
    with st.expander("➕ Ajouter un utilisateur", expanded=False):
      with st.form("add_user_form", clear_on_submit=True):
        new_username = st.text_input("Nom d'utilisateur").strip().upper()
        new_password = st.text_input("Mot de passe", type="password")
        new_role = st.selectbox("Rôle", ROLES_LIST)
        new_can_edit = st.checkbox("Droit de modification (can_edit)")
        new_projets_labels = st.multiselect(
            "📁 Projets autorisés",
            options=[PROJETS_LABELS[p] for p in PROJETS_LIST],
            default=[PROJETS_LABELS[projets_config.PROJET_PAR_DEFAUT]],
            help="L'utilisateur ne pourra voir/saisir des données que pour"
                 " le(s) projet(s) coché(s) ici (sans effet pour un rôle admin,"
                 " qui voit toujours tous les projets).",
        )
        new_projets = [
            p for p in PROJETS_LIST if PROJETS_LABELS[p] in new_projets_labels
        ]
        submit_add = st.form_submit_button(
            "Ajouter l'utilisateur", use_container_width=True, type="primary"
        )

        if submit_add:
          if not new_username:
            st.error("❌ Le nom d'utilisateur ne peut pas être vide.")
          elif not new_password:
            st.error("❌ Le mot de passe ne peut pas être vide.")
          elif new_username in st.session_state["users_db"]:
            st.warning(f"⚠️ L'utilisateur **{new_username}** existe déjà.")
          elif not new_projets and new_role != "admin":
            st.error("❌ Sélectionnez au moins un projet autorisé.")
          else:
            success, err = save_user_db(
                new_username, new_password, new_role, new_can_edit, new_projets
            )
            if success:
              st.session_state["users_db"] = load_users()
              st.success(
                  f"✅ Utilisateur **{new_username}** ajouté et synchronisé"
                  " sur le serveur !"
              )
              st.rerun()
            else:
              st.error(
                  f"❌ Erreur Supabase :\n\n`{err}`\n\n👉 *Vérifiez que RLS est"
                  " désactivé sur la table app_users dans Supabase.*"
              )

  with col_edit:
    with st.expander("✏️ Modifier un utilisateur", expanded=False):
      user_list = list(st.session_state["users_db"].keys())
      selected_user = st.selectbox(
          "Sélectionner un utilisateur", user_list, key="select_user_edit"
      )

      if selected_user:
        current_data = st.session_state["users_db"][selected_user]
        with st.form("edit_user_form"):
          mod_username = (
              st.text_input("Nom d'utilisateur", value=selected_user)
              .strip()
              .upper()
          )
          mod_password = st.text_input(
              "Nouveau mot de passe (laisser vide si inchangé)", type="password"
          )

          role_index = (
              ROLES_LIST.index(current_data["role"])
              if current_data["role"] in ROLES_LIST
              else 0
          )
          mod_role = st.selectbox("Rôle", ROLES_LIST, index=role_index)
          mod_can_edit = st.checkbox(
              "Droit de modification (can_edit)", value=current_data["can_edit"]
          )
          mod_projets_labels = st.multiselect(
              "📁 Projets autorisés",
              options=[PROJETS_LABELS[p] for p in PROJETS_LIST],
              default=[
                  PROJETS_LABELS[p]
                  for p in current_data.get("projets_autorises", [])
                  if p in PROJETS_LABELS
              ],
              help="Sans effet pour un rôle admin, qui voit toujours tous"
                   " les projets.",
          )
          mod_projets = [
              p for p in PROJETS_LIST if PROJETS_LABELS[p] in mod_projets_labels
          ]

          submit_edit = st.form_submit_button(
              "Enregistrer", use_container_width=True
          )

          if submit_edit:
            if not mod_username:
              st.error("❌ Le nom d'utilisateur ne peut pas être vide.")
            elif (
                mod_username != selected_user
                and mod_username in st.session_state["users_db"]
            ):
              st.error(
                  f"❌ Le nom d'utilisateur **{mod_username}** existe déjà."
              )
            elif not mod_projets and mod_role != "admin":
              st.error("❌ Sélectionnez au moins un projet autorisé.")
            else:
              updated_password = (
                  mod_password
                  if mod_password != ""
                  else current_data["password"]
              )

              success, err = save_user_db(
                  mod_username, updated_password, mod_role, mod_can_edit, mod_projets
              )
              if success:
                if mod_username != selected_user:
                  delete_user_db(selected_user)
                  if selected_user == current_username:
                    st.session_state["user"]["username"] = mod_username

                st.session_state["users_db"] = load_users()
                st.success(
                    f"✅ Utilisateur **{mod_username}** mis à jour et"
                    " synchronisé !"
                )
                st.rerun()
              else:
                st.error(f"❌ Erreur Supabase :\n\n`{err}`")

  with col_del:
    with st.expander("🗑️ Supprimer un utilisateur", expanded=False):
      user_list = list(st.session_state["users_db"].keys())
      user_to_delete = st.selectbox(
          "Sélectionner l'utilisateur à supprimer",
          user_list,
          key="select_user_del",
      )

      if user_to_delete:
        if user_to_delete == current_username:
          st.warning(
              "⚠️ Vous ne pouvez pas supprimer votre propre compte connecté."
          )
        else:
          if st.button(
              "Supprimer définitivement",
              type="primary",
              use_container_width=True,
          ):
            success, err = delete_user_db(user_to_delete)
            if success:
              st.session_state["users_db"] = load_users()
              st.success(
                  f"🗑️ Utilisateur **{user_to_delete}** supprimé définitivement."
              )
              st.rerun()
            else:
              st.error(f"❌ Erreur Supabase :\n\n`{err}`")

  st.markdown("---")

  st.session_state["users_db"] = load_users()
  data_users = []
  for user, details in st.session_state["users_db"].items():
    projets_de_lutilisateur = (
        "Tous (admin)" if details["role"] == "admin"
        else ", ".join(
            projets_config.nom_projet(p) for p in details.get("projets_autorises", [])
        ) or "—"
    )
    data_users.append({
        "Utilisateur": user,
        "Mot de Passe": details["password"],
        "Rôle": details["role"],
        "Droit de modification (can_edit)": details["can_edit"],
        "Projets autorisés": projets_de_lutilisateur,
    })

  st.dataframe(data_users, use_container_width=True)

elif page == "Chantiers" and current_role in projets_config.ROLES_CREATEURS_PROJET:
  st.title("🏗️ Mes Chantiers")
  _reg = projets_config.get_projets()
  _mes = projets_config.liste_projets_utilisateur(st.session_state["user"])
  st.caption(
      "Vous ne voyez que les chantiers auxquels vous avez accès."
      if current_role != "admin"
      else "En tant qu'administrateur, vous voyez tous les chantiers."
  )
  if _mes:
    st.dataframe(
        [
            {
                "Identifiant": p,
                "Chantier": _reg[p]["nom"],
                "Client": _reg[p]["client"],
                "Créé par": _reg[p].get("cree_par") or "-",
            }
            for p in _mes
        ],
        use_container_width=True,
        hide_index=True,
    )
  else:
    st.info("Aucun chantier pour le moment.")

  st.markdown("---")
  st.subheader("➕ Créer un nouveau chantier")
  with st.form("creer_chantier_form", clear_on_submit=True):
    nom_ch = st.text_input("Nom du chantier", placeholder="ex : Pont Oued Bouregreg")
    client_ch = st.text_input("Client", placeholder="ex : TGCC")
    submit_ch = st.form_submit_button("Créer le chantier", type="primary")
    if submit_ch:
      _u = st.session_state["users_db"].get(current_username)
      if current_role != "admin" and not _u:
        st.error(
            "❌ Votre compte n'est pas enregistré en base : un compte nommé est"
            " nécessaire pour créer un chantier."
        )
      else:
        ok, resultat = projets_config.creer_projet(
            supabase, nom_ch, client_ch, current_username
        )
        if not ok:
          st.error(f"❌ {resultat}")
        else:
          nouveau_pid = resultat
          if current_role != "admin":
            # Le créateur reçoit automatiquement l'accès à son nouveau chantier
            projets_maj = list(_u.get("projets_autorises", [])) + [nouveau_pid]
            ok_u, err_u = save_user_db(
                current_username, _u["password"], _u["role"], _u["can_edit"], projets_maj
            )
            if not ok_u:
              st.error(
                  f"⚠️ Chantier créé mais accès non attribué : {err_u}."
                  " Contactez un administrateur."
              )
              st.stop()
            st.session_state["users_db"] = load_users()
            st.session_state["user"]["projets_autorises"] = projets_maj
          st.session_state["projet_actif"] = nouveau_pid
          st.success(f"✅ Chantier « {nom_ch.strip()} » créé et sélectionné.")
          st.rerun()

elif page == "Suivi de Bétonnage":
  render_view(suivi_Betonnage, supabase)
elif page == "Suivi Contrôle Béton":
  render_view(suivi_controle_beton, supabase)
elif page == "Historique Complet & PVs":
  render_view(historique_pvs, supabase)
elif page == "Synthèse Béton":
  render_view(synthese_Beton, supabase)
