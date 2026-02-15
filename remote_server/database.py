import sqlite3
import os
from cryptography.fernet import Fernet
import json

DB_PATH = "bastet_vault.db"
# En prod, cette clé doit être dans une variable d'environnement !
# Pour le dev, on génère/charge une clé locale
KEY_FILE = "secret.key"

def load_key():
    if not os.path.exists(KEY_FILE):
        key = Fernet.generate_key()
        with open(KEY_FILE, "wb") as key_file:
            key_file.write(key)
    return open(KEY_FILE, "rb").read()

cipher = Fernet(load_key())

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Table Utilisateurs
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (username TEXT PRIMARY KEY, 
                  encrypted_creds BLOB,
                  metadata TEXT)''')
    # Table Images Visages
    c.execute('''CREATE TABLE IF NOT EXISTS faces
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT,
                  image_path TEXT,
                  FOREIGN KEY(username) REFERENCES users(username))''')
    conn.commit()
    conn.close()

def save_user_creds(username, password, intranet_url):
    data = json.dumps({"password": password, "url": intranet_url}).encode()
    encrypted = cipher.encrypt(data)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (username, encrypted_creds) VALUES (?, ?)", 
              (username, encrypted))
    conn.commit()
    conn.close()

def get_user_creds(username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT encrypted_creds FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    
    if row:
        decrypted = cipher.decrypt(row[0])
        return json.loads(decrypted.decode())
    return None

def add_face_image(username, image_path):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO faces (username, image_path) VALUES (?, ?)", (username, image_path))
    conn.commit()
    conn.close()

def get_faces():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, image_path FROM faces")
    rows = c.fetchall()
    conn.close()
    return [{"username": r[0], "image_path": r[1]} for r in rows]

# Init DB on module load
init_db()
