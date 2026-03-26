import sys
import os

print("="*50)
print("Configuration de la connexion MyGES pour Bastet AI")
print("="*50)

try:
    import keyring
except ImportError:
    print("Erreur : le module 'keyring' n'est pas installe.")
    print("Veuillez lancer : pip install keyring")
    sys.exit(1)

# Ensure 'core' can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from core.myges_integration import MyGesIntegration
except ImportError as e:
    print(f"Erreur d'importation du module MyGES : {e}")
    sys.exit(1)

m = MyGesIntegration()

print("\nNous allons configurer vos identifiants pour qu'ils soient securises")
print("par le gestionnaire de mots de passe de votre systeme (Windows Credential Manager).")
print("Ils ne seront JAMAIS stockes en texte clair.\n")

user = input("Identifiant MyGES (prenom.nom) : ").strip()

import getpass
pwd = getpass.getpass("Mot de passe (cache) : ")

print("\nTest de connexion en cours...")
success = m.login(user, pwd)

if success:
    print("\n✅ Connexion reussie !")
    print(f"✅ Identifiants enregistres. Il y a {len(m.schedule)} evenements dans votre agenda.")
    print("Bastet pourra desormais lire votre emploi du temps au demarrage.")
else:
    print("\n❌ Echec de la connexion. Verifiez vos identifiants ou l'etat des serveurs MyGES.")
