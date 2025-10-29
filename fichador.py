import os
import time
from asyncio import InvalidStateError
from dataclasses import dataclass
from typing import Final

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

from logging_config import get_logger


@dataclass
class FichajeResultado:
    """Representa el resultado de un intento de fichaje."""

    success: bool
    action: str
    message: str

LOGIN_URL: Final[str] = "https://fichaxe.usc.gal/pas/marcaxesDiarias"

logger = get_logger(__name__)


def fichar(accion: str) -> FichajeResultado:
    """Realiza el fichaje indicado si procede y devuelve el resultado."""

    accion = accion.lower().strip()
    if accion not in {"entrada", "salida"}:
        raise ValueError("La acción de fichaje debe ser 'entrada' o 'salida'.")

    logger.info("Iniciando proceso de fichaje de %s", accion)

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

        ultima_fila = filas[-1] if filas else None
        celdas_antes = ultima_fila.find_elements(By.TAG_NAME, "td") if ultima_fila else []
        entrada_antes = celdas_antes[0].text.strip() if len(celdas_antes) > 0 else "-"
        salida_antes = celdas_antes[1].text.strip() if len(celdas_antes) > 1 else "-"

        if entrada_antes == "-":
            if salida_antes == "-":
                accion_permitida = "entrada"
            else:
                raise InvalidStateError()
        else:
            if salida_antes == "-":
                accion_permitida = "salida"
            else:
                accion_permitida = "entrada"

        logger.info("Acción permitida actualmente en la web: %s", accion_permitida)

        if accion != accion_permitida:
            if accion_permitida == "salida":
                mensaje = (
                    "⚠️ Ya existe una entrada pendiente de cerrar. Marca la salida antes de "
                    "registrar una nueva entrada."
                )
            else:
                mensaje = "⚠️ No hay una entrada pendiente para cerrar."
            logger.warning("Acción '%s' no permitida en este momento", accion)
            return FichajeResultado(False, accion, mensaje)

        # --- CLIC EN NOVA MARCAXE ---
        nova_btn = driver.find_element(By.ID, "novaMarcaxe")
        # driver.execute_script("arguments[0].click();", nova_btn)
        logger.info("Clic en 'novaMarcaxe' ejecutado")
        time.sleep(5)

        # --- REFRESCAR Y VERIFICAR CAMBIO ---
        driver.refresh()
        wait.until(EC.presence_of_element_located((By.ID, "taboaMarcaxesPropios")))
        tabla = driver.find_element(By.ID, "taboaMarcaxesPropios")
        filas_despues = tabla.find_elements(By.TAG_NAME, "tr")
        ultima_fila_despues = filas_despues[-1] if filas_despues else None
        celdas_despues = (
            ultima_fila_despues.find_elements(By.TAG_NAME, "td") if ultima_fila_despues else []
        )

        if accion == "entrada":
            entrada_despues = celdas_despues[0].text.strip() if len(celdas_despues) > 0 else "-"
            if entrada_despues and entrada_despues != entrada_antes:
                logger.info("Entrada registrada a las %s", entrada_despues)
                return FichajeResultado(
                    True, accion, f"✅ Fichaje de entrada registrado a las {entrada_despues}"
                )

            if len(filas_despues) > len(filas):
                logger.info("Entrada detectada en nueva fila tras el fichaje")
                return FichajeResultado(
                    True,
                    accion,
                    f"✅ Fichaje de entrada registrado a las {entrada_despues or 'hora desconocida'}",
                )

            logger.warning("No se detectó una hora de entrada tras intentar fichar.")
            return FichajeResultado(
                False,
                accion,
                "⚠️ No se confirmó el fichaje de entrada (puede que ya estuviese registrado).",
            )

        salida_despues = celdas_despues[1].text.strip() if len(celdas_despues) > 1 else "-"
        if salida_despues != '-' and salida_despues != salida_antes:
            logger.info("Salida registrada a las %s", salida_despues)
            return FichajeResultado(
                True, accion, f"✅ Fichaje de salida registrado a las {salida_despues}"
            )

        logger.warning("No se detectó una hora de salida tras intentar fichar.")
        return FichajeResultado(
            False,
            accion,
            "⚠️ No se confirmó el fichaje de salida (puede que ya estuviese registrado).",
        )

    except Exception as e:
        logger.exception("Error durante el proceso de fichaje")
        return FichajeResultado(False, accion, f"❌ Error en fichaje: {e}")

    finally:
        driver.quit()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    resultado = fichar("entrada")
    print(f"Resultado: {resultado.success}, Acción: {resultado.action}, Mensaje: {resultado.message}")