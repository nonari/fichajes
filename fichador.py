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
    """Realiza el fichaje y devuelve un mensaje de resultado, detectando entrada o salida."""
    logger.info("Iniciando proceso de fichaje")

    user = os.getenv("USC_USER")
    password = os.getenv("USC_PASS")

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    wait = WebDriverWait(driver, 20)

    try:
        # --- LOGIN ---
        driver.get(LOGIN_URL)
        logger.info("Página de login cargada")

        user_input = wait.until(EC.presence_of_element_located((By.ID, "username-input")))
        pass_input = driver.find_element(By.ID, "password")
        user_input.send_keys(user)
        pass_input.send_keys(password)
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        logger.info("Credenciales introducidas")

        # --- PÁGINA PRINCIPAL ---
        wait.until(EC.presence_of_element_located((By.ID, "novaMarcaxe")))
        tabla = driver.find_element(By.ID, "taboaMarcaxesPropios")
        filas = tabla.find_elements(By.TAG_NAME, "tr")

        if not filas:
            logger.warning("No se encontraron filas de marcaxe; puede ser el primer fichaje del día.")
            tipo_accion = "entrada"
            salida_antes = None
        else:
            ultima_fila = filas[-1]
            celdas = ultima_fila.find_elements(By.TAG_NAME, "td")
            entrada, salida = [c.text.strip() for c in celdas[:2]]
            tipo_accion = "salida" if entrada and not salida else "entrada"
            salida_antes = salida

        logger.info("Detectado tipo de fichaje: %s", tipo_accion)

        # --- CLIC EN NOVA MARCAXE ---
        nova_btn = driver.find_element(By.ID, "novaMarcaxe")
        driver.execute_script("arguments[0].click();", nova_btn)
        logger.info("Clic en 'novaMarcaxe' ejecutado")
        time.sleep(5)

        # --- REFRESCAR Y VERIFICAR CAMBIO ---
        driver.refresh()
        wait.until(EC.presence_of_element_located((By.ID, "taboaMarcaxesPropios")))
        tabla = driver.find_element(By.ID, "taboaMarcaxesPropios")
        filas_despues = tabla.find_elements(By.TAG_NAME, "tr")

        if tipo_accion == "entrada":
            if len(filas_despues) > len(filas):
                ultima_fila = filas_despues[-1]
                hora_entrada = ultima_fila.find_elements(By.TAG_NAME, "td")[0].text
                logger.info("Entrada registrada a las %s", hora_entrada)
                return f"✅ Fichaje de entrada registrado a las {hora_entrada}"
            else:
                logger.warning("No se detectó una nueva fila tras fichar entrada.")
                return "⚠️ No se confirmó el fichaje de entrada (quizás ya marcado hoy)."

        else:  # tipo_accion == "salida"
            ultima_fila = filas_despues[-1]
            celdas = ultima_fila.find_elements(By.TAG_NAME, "td")
            salida_despues = celdas[1].text.strip()
            if salida_despues and salida_despues != salida_antes:
                logger.info("Salida registrada a las %s", salida_despues)
                return f"✅ Fichaje de salida registrado a las {salida_despues}"
            else:
                logger.warning("No se detectó cambio en la columna 'Saída'.")
                return "⚠️ No se confirmó el fichaje de salida (quizás ya registrado o error en página)."

    except Exception as e:
        logger.exception("Error durante el proceso de fichaje")
        return f"❌ Error en fichaje: {e}"

    finally:
        driver.quit()
