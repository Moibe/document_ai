from google.auth import default
from google.auth.transport.requests import Request


# Obtener credenciales y el token de acceso
# Esto busca automáticamente las credenciales de tu entorno (ej. cuenta de servicio)
credentials, _ = default()
credentials.refresh(Request())
access_token = credentials.token

print("Access token:")
print(access_token)