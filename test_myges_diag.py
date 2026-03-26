import os, sys
sys.path.insert(0, '.')
from core.myges_integration import MyGesIntegration

m = MyGesIntegration()
user, pwd = m.get_credentials()
print(f"user_config.txt exists at root: {os.path.exists('user_config.txt')}")
print(f"Username found: {repr(user)}")
print(f"Password found (masked): {'***' if pwd else repr(pwd)}")

if not user:
    print("\nDIAGNOSTIC: Aucun user_config.txt trouve a la racine du projet.")
    print("La connexion MyGES echoue silencieusement au demarrage.")
else:
    print("\nTest de login MyGES...")
    result = m.login(user, pwd)
    print(f"Login result: {result}")
    if result:
        print(f"Agenda events: {len(m.schedule)}")
        print(m.get_context_string())
