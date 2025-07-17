# firebase_setup.py
import firebase_admin
from firebase_admin import credentials
import os
import json
from dotenv import load_dotenv
load_dotenv()

# Initialize Firebase if not already initialized
if not firebase_admin._apps:
    cred_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not cred_json:
        raise ValueError("Missing Firebase credentials!")
    cred_dict = json.loads(cred_json)
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred)