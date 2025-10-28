import os
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

LOGIN_URL = "https://fichaxe.usc.gal/pas/marcaxesDiarias"

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

    try:
        driver.get(LOGIN_URL)
        user_input = wait.until(EC.presence_of_element_located((By.ID, "username-input")))
        pass_input = driver.find_element(By.ID, "password")
        user_input.send_keys(user)
        pass_input.send_keys(password)
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

        wait.until(EC.presence_of_element_located((By.ID, "novaMarcaxe")))
        nova_btn = driver.find_element(By.ID, "novaMarcaxe")
        driver.execute_script("arguments[0].click();", nova_btn)
        time.sleep(5)

        return "✅ Fichaje realizado con éxito"
    except Exception as e:
        return f"❌ Error en fichaje: {e}"
    finally:
        driver.quit()
