import json
import os
import time
from datetime import datetime
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError
from playwright.sync_api import sync_playwright, Page, BrowserContext

from opt_token import get_opt_token
from dotenv import load_dotenv

load_dotenv()


def _get_env_var(name: str, default: str | None = None, required: bool = False) -> str | None:
    """
    Fetch an environment variable, optionally enforcing presence.
    """
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"{name} is required. Add it to your environment or .env file.")
    return value


def _get_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer. Received: {raw}") from exc


# Configuration
LOGIN_URL = _get_env_var("INVIU_LOGIN_URL", default="https://asesor.inviu.com.ar/login")
EMAIL = _get_env_var("EMAIL", required=True)
PASSWORD = _get_env_var("PASSWORD", required=True)
WAIT_TIME_FOR_TOKEN = _get_int_env("OTP_WAIT_SECONDS", 2 * 60)
PLAYWRIGHT_HEADLESS = _get_bool_env("PLAYWRIGHT_HEADLESS", default=False)
LOCAL_STORAGE_TOKEN_KEY = _get_env_var("TOKENS_KEY", default="TOKENS_KEY")
API_BASE_URL = _get_env_var("INVIU_API_BASE_URL", default="https://inviuxy.inviu.com.ar")

# S3 Configuration
S3_BUCKET_NAME = _get_env_var("S3_BUCKET_NAME", default="dracma-data-lake")
S3_REGION = _get_env_var("S3_REGION", default="us-east-2")
S3_PREFIX = _get_env_var("S3_PREFIX", default="raw")


# ============================================================================
# OTP Token Functions
# ============================================================================

def wait_for_token(wait_time: int = WAIT_TIME_FOR_TOKEN) -> str:
    """
    Wait for the email token to arrive and retrieve it from Microsoft Graph Mail
    
    Args:
        wait_time: Time to wait in seconds (default: 2 minutes)
        
    Returns:
        OTP token string
    """
    time.sleep(wait_time)
    token = get_opt_token()
    if not token:
        raise RuntimeError("No OTP token received from get_opt_token()")
    return token


# ============================================================================
# Token Extraction Functions
# ============================================================================

def _extract_tokens_from_local_storage(page: Page, key: str) -> dict[str, str] | None:
    """
    Extract both idToken and refreshToken from localStorage JSON value.
    Returns a dict with 'idToken' and 'refreshToken' keys, or None if not found.
    """
    raw_value = page.evaluate("(k) => window.localStorage.getItem(k)", key)
    if not raw_value:
        return None
    
    try:
        tokens_data = json.loads(raw_value)
        id_token = tokens_data.get("idToken")
        refresh_token = tokens_data.get("refreshToken")
        
        if id_token and refresh_token:
            return {
                "idToken": id_token,
                "refreshToken": refresh_token
            }
        return None
    except (json.JSONDecodeError, AttributeError):
        return None


def _extract_token_from_local_storage(page: Page, key: str) -> str | None:
    """
    Extract the idToken from localStorage JSON value (backward compatibility).
    Returns the idToken string or None if not found.
    """
    tokens = _extract_tokens_from_local_storage(page, key)
    return tokens.get("idToken") if tokens else None


# ============================================================================
# Token Refresh Functions
# ============================================================================

def refresh_token(id_token: str, refresh_token: str) -> str | None:
    """
    Refresh the authentication token using the refresh token endpoint.
    
    Args:
        id_token: Current idToken
        refresh_token: Current refreshToken
        
    Returns:
        New idToken if refresh successful, None otherwise
    """
    endpoint = "/advisor/auth/refresh"
    url = f"{API_BASE_URL}{endpoint}"
    payload = {
        "idToken": id_token,
        "refreshToken": refresh_token
    }
    
    try:
        print(f"🔄 Refrescando token...")
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        
        response_data = response.json()
        new_id_token = response_data.get("idToken")
        
        if new_id_token:
            print("✅ Token refrescado exitosamente")
            return new_id_token
        else:
            print("⚠️ Respuesta de refresh no contiene idToken")
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Error al refrescar token: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Response: {e.response.text[:200]}")
        return None


# ============================================================================
# API Call Functions
# ============================================================================

def _make_api_call(token: str, endpoint: str, method: str = "GET", **kwargs) -> dict | None:
    """
    Make an API call using the Bearer token.
    
    Args:
        token: The Bearer token to use for authorization
        endpoint: API endpoint path (e.g., "/advisor/clients/accounts/CVAL")
        method: HTTP method (GET, POST, PUT, DELETE, etc.)
        **kwargs: Additional arguments to pass to requests (data, json, params, etc.)
        
    Returns:
        Response JSON as dict, or None on error
    """
    url = f"{API_BASE_URL}{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        response = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ API call failed ({method} {endpoint}): {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Response: {e.response.text[:200]}")
        return None


def _get_s3_client():
    """
    Get or create S3 client.
    
    Returns:
        boto3 S3 client
    """
    return boto3.client('s3', region_name=S3_REGION)


def _save_api_response(endpoint: str, response_data: dict, method: str = "GET", name: str | None = None) -> str:
    """
    Save API response to S3 bucket.
    
    Args:
        endpoint: API endpoint path
        response_data: Response data to save
        method: HTTP method used
        name: Friendly name for the endpoint (used in filename)
        
    Returns:
        S3 key (path) of the saved file
    """
    # Create filename from name (or endpoint if name not provided) + date
    if name:
        filename_base = name
    else:
        # Fallback to sanitized endpoint if no name provided
        filename_base = endpoint.replace("/", "_").replace("\\", "_").strip("_").replace("?", "_")
    
    date = datetime.now().strftime("%Y%m%d")
    filename = f"{filename_base}_{date}.json"
    
    # Create S3 key with prefix
    s3_key = f"{S3_PREFIX}/{filename}"
    
    # Prepare response with metadata
    output = {
        "timestamp": datetime.now().isoformat(),
        "method": method,
        "endpoint": endpoint,
        "url": f"{API_BASE_URL}{endpoint}",
        "data": response_data
    }
    
    # Convert to JSON string
    json_content = json.dumps(output, indent=2, ensure_ascii=False)
    
    try:
        # Upload to S3
        s3_client = _get_s3_client()
        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key,
            Body=json_content.encode('utf-8'),
            ContentType='application/json'
        )
        
        s3_path = f"s3://{S3_BUCKET_NAME}/{s3_key}"
        return s3_path
        
    except ClientError as e:
        print(f"❌ Error uploading to S3: {e}")
        raise


def perform_api_calls(token: str, endpoints: list[dict]) -> dict[str, dict | None]:
    """
    Perform multiple API calls and save responses.
    
    Args:
        token: Bearer token for authentication
        endpoints: List of endpoint configs, each with:
            - "endpoint": API path (required)
            - "method": HTTP method (default: "GET")
            - "name": Friendly name for logging (optional)
            - Additional kwargs for requests (data, json, params, etc.)
    
    Returns:
        Dictionary mapping endpoint names to responses (or None if failed)
    """
    results = {}
    
    for config in endpoints:
        endpoint = config["endpoint"]
        method = config.get("method", "GET")
        name = config.get("name", endpoint)
        
        print(f"📡 Calling API: {method} {endpoint}")
        
        # Extract request kwargs (everything except endpoint, method, name)
        request_kwargs = {k: v for k, v in config.items() 
                         if k not in ("endpoint", "method", "name")}
        
        response = _make_api_call(token, endpoint, method, **request_kwargs)
        
        if response:
            print(f"✅ API call successful: {name}")
            filepath = _save_api_response(endpoint, response, method, name=name)
            print(f"💾 Response saved to: {filepath}")
            results[name] = response
        else:
            print(f"❌ API call failed: {name}")
            results[name] = None
    
    return results


# ============================================================================
# Login Flow Functions
# ============================================================================

def _fill_login_form(page: Page) -> None:
    """Fill the login form with email and password."""
    # Esperar inputs Angular
    page.wait_for_selector('input[formcontrolname="email"]', timeout=20000)
    page.wait_for_selector('input[formcontrolname="password"]', timeout=20000)

    # Escribir como humano
    page.click('input[formcontrolname="email"]')
    page.keyboard.type(EMAIL, delay=50)

    page.click('input[formcontrolname="password"]')
    page.keyboard.type(PASSWORD, delay=50)


def _submit_login(page: Page) -> int:
    """
    Submit the login form and wait for response.
    
    Returns:
        HTTP status code of the login response
    """
    # Esperar que el botón se habilite
    submit_btn = 'tui-button[type="primary"] button[type="submit"]'
    page.wait_for_function(
        """() => {
            const btn = document.querySelector('tui-button[type="primary"]');
            return btn && !btn.hasAttribute('disabled');
        }""",
        timeout=15000
    )

    print("🔐 Enviando login...")

    # 🔑 Esperar la response REAL del backend
    with page.expect_response(
        lambda r: "/advisor/auth/login/v4" in r.url,
        timeout=20000
    ) as response_info:
        page.click(submit_btn)

    response = response_info.value
    print("📡 Backend status:", response.status)
    return response.status


def _handle_otp_challenge(page: Page) -> int:
    """
    Handle the OTP challenge step.
    
    Returns:
        HTTP status code of the challenge response
    """
    time.sleep(2)
    
    # Esperar estar en la pantalla de challenge
    page.wait_for_url("**/challenge-code/**", timeout=30000)
    print("🧩 Challenge detectado")

    # Esperar input del código
    otp_input = 'input[formcontrolname="newPassword"]'
    page.wait_for_selector(otp_input, timeout=20000)

    # Obtener el código
    otp_code = wait_for_token(WAIT_TIME_FOR_TOKEN)

    # Escribir como humano para que Angular valide
    page.click(otp_input)
    page.keyboard.type(otp_code, delay=80)

    # Esperar que el botón se habilite
    submit_btn = 'tui-button[type="primary"] button[type="submit"]'
    page.wait_for_function(
        """() => {
            const btn = document.querySelector('tui-button[type="primary"]');
            return btn && !btn.hasAttribute('disabled');
        }""",
        timeout=15000
    )

    print("🚀 Enviando challenge...")

    # Enviar y esperar respuesta backend del challenge
    with page.expect_response(
        lambda r: "challenge" in r.url.lower(),
        timeout=20000
    ) as response_info:
        page.click(submit_btn)

    response = response_info.value
    print("📡 Challenge response status:", response.status)
    
    # Esperar salir del challenge
    page.wait_for_url(
        lambda url: "challenge-code" not in url,
        timeout=30000
    )

    print("🎉 Challenge completado, URL final:", page.url)
    return response.status


def perform_login() -> dict[str, str] | None:
    """
    Perform the complete login flow.
    
    Returns:
        Dictionary with 'idToken' and 'refreshToken' if login successful, None otherwise
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=PLAYWRIGHT_HEADLESS,
            args=["--disable-blink-features=AutomationControlled"]
        )

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        )

        page = context.new_page()

        print("🌐 Abriendo login...")
        page.goto(
            LOGIN_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        # Fill and submit login form
        _fill_login_form(page)
        login_status = _submit_login(page)

        if login_status != 200:
            print("❌ Login falló en el primer paso")
            browser.close()
            return None

        # Handle OTP challenge
        challenge_status = _handle_otp_challenge(page)

        if challenge_status == 200 and "/login" not in page.url.lower():
            print("✅ Login OK")
            
            # Guardar sesión final (ya autenticado)
            context.storage_state(path="session_state.json")
            print("💾 Sesión guardada en session_state.json")
            
            # Extraer tokens de localStorage
            tokens = _extract_tokens_from_local_storage(page, LOCAL_STORAGE_TOKEN_KEY)
            if tokens:
                print(f"🔑 Tokens extraídos de localStorage[{LOCAL_STORAGE_TOKEN_KEY}]")
                browser.close()
                return tokens
            else:
                print(f"⚠️ No se encontraron tokens en localStorage para la key {LOCAL_STORAGE_TOKEN_KEY}")
                browser.close()
                return None
        else:
            print("❌ Login falló en el challenge")
            browser.close()
            return None


# ============================================================================
# Main Execution
# ============================================================================

def get_api_endpoints() -> list[dict]:
    """
    Define the API endpoints to call after login.
    Can be overridden via environment variable or extended here.
    
    Returns:
        List of endpoint configurations
    """
    # Default endpoint
    endpoints = [
        {
            "endpoint": "/advisor/clients/accounts/v2/CVAL",
            "method": "GET",
            "name": "cuentas"
        },
        {
            "endpoint": "/advisor/clients/all",
            "method": "GET",
            "name": "cartera-aranceles"
        },
        {
            "endpoint": "/advisor/clients/movements?custodian=CVAL",
            "method": "GET",
            "name": "depositos_y_retiros-movimientos"
        },
        {
            "endpoint": "/advisor/operations/cval",
            "method": "GET",
            "name": "operaciones"
        },

        {
            "endpoint": "/advisor/holdings/v2/CVAL?term=24HS",
            "method": "GET",
            "name": "tendencias"
        },
        {
            "endpoint": "/advisor/clients/balances/v2",
            "method": "GET",
            "name": "saldos"
        },

    ]
    
    return endpoints


def main() -> None:
    """Main execution function."""
    # Perform login
    tokens = perform_login()
    
    if not tokens:
        print("❌ No se pudo obtener los tokens. Abortando llamadas API.")
        return
    
    id_token = tokens["idToken"]
    refresh_token_value = tokens["refreshToken"]
    
    # Refresh token before making API calls
    print("\n" + "="*60)
    print("🔄 Refrescando token")
    print("="*60)
    
    refreshed_token = refresh_token(id_token, refresh_token_value)
    
    if not refreshed_token:
        print("⚠️ No se pudo refrescar el token. Usando token original.")
        active_token = id_token
    else:
        active_token = refreshed_token
    
    # Get API endpoints to call
    endpoints = get_api_endpoints()
    
    # Perform API calls
    print("\n" + "="*60)
    print("🚀 Iniciando llamadas API")
    print("="*60)
    
    results = perform_api_calls(active_token, endpoints)
    
    # Summary
    print("\n" + "="*60)
    print("📊 Resumen de llamadas API")
    print("="*60)
    for name, response in results.items():
        status = "✅" if response else "❌"
        print(f"{status} {name}: {'Exitoso' if response else 'Falló'}")


if __name__ == "__main__":
    main()