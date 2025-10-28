import os
import time
from typing import Final

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

from logging_config import get_logger

LOGIN_URL: Final[str] = "https://fichaxe.usc.gal/pas/marcaxesDiarias"

logger = get_logger(__name__)

def fichar() -> str:
    """Realiza el fichaje y devuelve un mensaje de resultado."""
    user = os.getenv("USC_USER")
    password = os.getenv("USC_PASS")

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    wait = WebDriverWait(driver, 20)

    logger.info("Iniciando proceso de fichaje")

    try:
        driver.get(LOGIN_URL)
        logger.info("Página de login cargada")
        user_input = wait.until(EC.presence_of_element_located((By.ID, "username-input")))
        pass_input = driver.find_element(By.ID, "password")
        user_input.send_keys(user)
        pass_input.send_keys(password)
        logger.info("Credenciales introducidas")
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

        wait.until(EC.presence_of_element_located((By.ID, "novaMarcaxe")))
        nova_btn = driver.find_element(By.ID, "novaMarcaxe")
        logger.info("Botón 'novaMarcaxe' localizado, acción de click deshabilitada temporalmente")
        logger.debug("Elemento encontrado: %s", nova_btn)
        time.sleep(5)

        logger.info("Fichaje simulado correctamente")
        return "ℹ️ Intento de fichaje registrado (acción deshabilitada)"
    except Exception as e:
        logger.exception("Error durante el proceso de fichaje")
        return f"❌ Error en fichaje: {e}"
    finally:
        driver.quit()
