from dotenv import load_dotenv
from google.auth import default
from google.auth.transport.requests import Request

#load_dotenv()
#DOCUMENT_AI_SCOPE = scopes.CLOUD_PLATFORM

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

credentials, _ = default()
#credentials, _ = default(scopes=[DOCUMENT_AI_SCOPE])
#credentials, _ = default(scopes=SCOPES)
credentials.refresh(Request())
access_token = credentials.token