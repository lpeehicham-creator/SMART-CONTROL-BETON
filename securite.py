"""
Sécurité des mots de passe : hachage PBKDF2-SHA256 avec sel aléatoire.

N'utilise que la bibliothèque standard (aucune dépendance à installer).
Format stocké :  pbkdf2_sha256$<itérations>$<sel base64>$<hash base64>

Compatibilité : un mot de passe ancien (texte clair) est encore reconnu à la
connexion, puis converti automatiquement en haché (cf. doit_etre_rehache).
"""

import base64
import hashlib
import hmac
import os

ALGO = "pbkdf2_sha256"
ITERATIONS = 600_000          # recommandation OWASP 2023 pour PBKDF2-SHA256
LONGUEUR_MINI = 8


def _b64(octets):
    return base64.b64encode(octets).decode("ascii")


def hasher_mot_de_passe(mot_de_passe, iterations=ITERATIONS):
    """Retourne le haché (avec sel et nombre d'itérations) d'un mot de passe."""
    sel = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", str(mot_de_passe).encode("utf-8"), sel, iterations)
    return f"{ALGO}${iterations}${_b64(sel)}${_b64(dk)}"


def est_hache(valeur):
    """True si la valeur stockée est déjà au format haché."""
    return (
        isinstance(valeur, str)
        and valeur.startswith(ALGO + "$")
        and valeur.count("$") == 3
    )


def verifier_mot_de_passe(mot_de_passe, stocke):
    """Compare un mot de passe saisi à la valeur stockée (hachée, ou ancien
    format texte clair). Comparaison à temps constant."""
    if mot_de_passe is None or not isinstance(stocke, str) or stocke == "":
        return False
    if est_hache(stocke):
        try:
            _, iterations, sel_b64, hash_b64 = stocke.split("$")
            attendu = base64.b64decode(hash_b64)
            calcule = hashlib.pbkdf2_hmac(
                "sha256", str(mot_de_passe).encode("utf-8"),
                base64.b64decode(sel_b64), int(iterations),
            )
            return hmac.compare_digest(calcule, attendu)
        except Exception:
            return False
    # Ancien format : texte clair (à convertir dès que possible)
    return hmac.compare_digest(str(mot_de_passe).encode("utf-8"), stocke.encode("utf-8"))


def doit_etre_rehache(stocke):
    """True si la valeur stockée est en clair ou utilise moins d'itérations
    que le réglage actuel."""
    if not est_hache(stocke):
        return True
    try:
        return int(stocke.split("$")[1]) < ITERATIONS
    except Exception:
        return True


def valider_mot_de_passe(mot_de_passe, username=""):
    """Règles pour un NOUVEAU mot de passe. Retourne un message d'erreur, ou
    None si le mot de passe est accepté."""
    mdp = mot_de_passe or ""
    if len(mdp) < LONGUEUR_MINI:
        return f"Le mot de passe doit contenir au moins {LONGUEUR_MINI} caractères."
    if username and mdp.strip().lower() == str(username).strip().lower():
        return "Le mot de passe ne doit pas être identique au nom d'utilisateur."
    return None
