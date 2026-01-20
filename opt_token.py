#!/usr/bin/env python3
"""
Read OTP token from Microsoft Graph Mail
"""

import os
import re
import requests
from typing import Optional
from dotenv import load_dotenv
import msal

from utils import setup_logger

# Load environment variables from .env if present
load_dotenv()

# Configuration from environment
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
MAILBOX_UPN = os.getenv("MAILBOX_UPN")

# Validación básica
for k, v in {
    "TENANT_ID": TENANT_ID,
    "CLIENT_ID": CLIENT_ID,
    "CLIENT_SECRET": CLIENT_SECRET,
    "MAILBOX_UPN": MAILBOX_UPN,
}.items():
    if not v:
        raise RuntimeError(f"Falta variable de entorno: {k}")


def get_graph_token() -> str:
    """
    Obtener token de Microsoft Graph usando Client Credentials.
    
    Returns:
        Access token string
    """
    app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )

    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )

    if "access_token" not in result:
        raise RuntimeError(f"Error obteniendo token: {result}")

    return result["access_token"]


def read_last_messages(token: str, top: int = 10) -> list:
    """
    Leer últimos mensajes de Graph Mail.
    
    Args:
        token: Access token de Microsoft Graph
        top: Número de mensajes a leer
        
    Returns:
        Lista de mensajes
    """
    url = f"https://graph.microsoft.com/v1.0/users/{MAILBOX_UPN}/messages"
    params = {
        "$top": str(top),
        "$orderby": "receivedDateTime DESC",
        "$select": "id,subject,from,receivedDateTime,body,bodyPreview",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()["value"]


def get_message_body(token: str, message_id: str) -> str:
    """
    Obtener el body completo de un mensaje.
    
    Args:
        token: Access token de Microsoft Graph
        message_id: ID del mensaje
        
    Returns:
        Body del mensaje como string
    """
    url = f"https://graph.microsoft.com/v1.0/users/{MAILBOX_UPN}/messages/{message_id}"
    params = {
        "$select": "body",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    message = resp.json()
    
    # El body puede venir en formato HTML o texto
    body_content = message.get("body", {})
    body_text = body_content.get("content", "")
    
    # Si es HTML, intentar extraer texto básico
    if body_content.get("contentType") == "html":
        # Remover tags HTML básicos (simple approach)
        body_text = re.sub(r'<[^>]+>', '', body_text)
        body_text = body_text.replace('&nbsp;', ' ').replace('&amp;', '&')
    
    return body_text


def extract_otp_from_text(text: str) -> Optional[str]:
    """
    Extraer código OTP del texto del email.
    Busca números de 4-8 dígitos que podrían ser códigos OTP.
    
    Args:
        text: Texto del email
        
    Returns:
        Código OTP encontrado o None
    """
    # Buscar patrones comunes de OTP:
    # - Números de 4-8 dígitos
    # - Palabras clave como "código", "code", "OTP", "token" seguidas de números
    patterns = [
        r'\b(\d{6})\b',  # 6 dígitos (más común)
        r'\b(\d{4,8})\b',  # 4-8 dígitos
        r'(?:código|code|OTP|token)[\s:]*(\d{4,8})',  # Con palabra clave
        r'(\d{4,8})(?:\s|$)',  # Al final de línea o seguido de espacio
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            # Retornar el primer match que tenga 6 dígitos, o el primero disponible
            for match in matches:
                if len(match) >= 6:
                    return match
            return matches[0]
    
    return None


def get_opt_token() -> Optional[str]:
    """
    Obtener el código OTP del email más reciente usando Microsoft Graph Mail.
    
    Returns:
        Código OTP encontrado o None
    """
    logger = setup_logger("dracma.login_inviu", "INFO")
    
    try:
        logger.info("Obteniendo token de Microsoft Graph...")
        token = get_graph_token()
        logger.info("Token obtenido exitosamente")
        
        logger.info(f"Leyendo mensajes de: {MAILBOX_UPN}")
        messages = read_last_messages(token, top=10)
        
        if not messages:
            logger.warning("No se encontraron mensajes")
            return None
        
        # Buscar OTP en los mensajes más recientes
        for message in messages:
            message_id = message.get("id")
            subject = message.get("subject", "")
            body_preview = message.get("bodyPreview", "")
            
            logger.info(f"Revisando mensaje: {subject[:50]}...")
            
            # Primero intentar con el preview
            otp = extract_otp_from_text(body_preview)
            if otp:
                logger.info(f"OTP encontrado en preview: {otp}")
                return otp
            
            # Si no se encuentra en el preview, leer el body completo
            if message_id:
                try:
                    body_text = get_message_body(token, message_id)
                    otp = extract_otp_from_text(body_text)
                    if otp:
                        logger.info(f"OTP encontrado en body completo: {otp}")
                        return otp
                except Exception as e:
                    logger.warning(f"Error leyendo body completo del mensaje {message_id}: {e}")
                    continue
        
        logger.warning("No se encontró código OTP en los mensajes revisados")
        return None
        
    except Exception as e:
        logger.error("Error obteniendo OTP de Graph Mail", exc_info=True)
        raise


if __name__ == "__main__":
    token = get_opt_token()
    if token:
        print(f"Token encontrado: {token}")
    else:
        print("No se encontró token")
