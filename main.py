from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from playwright.async_api import async_playwright
import asyncio
import re
from typing import Optional

app = FastAPI(title="Consulta CO - SIMIT & RUNT API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConsultaRequest(BaseModel):
    identificador: str
    tipo: str  # "simit", "runt_licencia", "runt_vehiculo"


# ─── SIMIT ────────────────────────────────────────────────────────────────────
async def consultar_simit(cedula: str) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        try:
            await page.goto("https://www.fcm.org.co/simit/#/estado-cuenta", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=20000)

            # Esperar campo de búsqueda
            await page.wait_for_selector("input[placeholder*='identificación'], input[name*='documento'], input[type='text']", timeout=15000)

            # Llenar cédula
            campo = await page.query_selector("input[placeholder*='identificación'], input[name*='documento'], input[type='text']")
            await campo.fill(cedula)
            await page.keyboard.press("Enter")

            # Esperar resultado
            await page.wait_for_timeout(4000)

            # Intentar extraer datos de multas
            contenido = await page.content()

            resultado = {
                "cedula": cedula,
                "fuente": "SIMIT",
                "tiene_multas": False,
                "total_deuda": "0",
                "comparendos": [],
                "estado": "sin_multas",
                "raw_text": ""
            }

            # Detectar si hay deuda
            if any(x in contenido.lower() for x in ["comparendo", "infracción", "multa", "deuda", "valor"]):
                resultado["tiene_multas"] = True
                resultado["estado"] = "con_multas"

                # Extraer filas de tabla si existen
                rows = await page.query_selector_all("table tbody tr")
                comparendos = []
                for row in rows[:20]:
                    cells = await row.query_selector_all("td")
                    if cells:
                        row_data = []
                        for cell in cells:
                            text = await cell.inner_text()
                            row_data.append(text.strip())
                        if any(row_data):
                            comparendos.append(row_data)
                resultado["comparendos"] = comparendos

                # Intentar extraer total
                total_el = await page.query_selector("[class*='total'], [class*='deuda'], [class*='valor']")
                if total_el:
                    total_text = await total_el.inner_text()
                    nums = re.findall(r'[\d.,]+', total_text)
                    if nums:
                        resultado["total_deuda"] = nums[-1]

            # Detectar "no tiene multas"
            if any(x in contenido.lower() for x in ["no tiene", "no registra", "sin multas", "no hay comparendos"]):
                resultado["tiene_multas"] = False
                resultado["estado"] = "sin_multas"

            return resultado

        except Exception as e:
            return {"cedula": cedula, "fuente": "SIMIT", "estado": "error", "error": str(e)}
        finally:
            await browser.close()


# ─── RUNT LICENCIA ───────────────────────────────────────────────────────────
async def consultar_runt_licencia(cedula: str) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        try:
            await page.goto(
                "https://portalpublico.runt.gov.co/#/consulta-ciudadano-documento/consulta/consulta-ciudadano-documento",
                timeout=30000
            )
            await page.wait_for_load_state("networkidle", timeout=20000)
            await page.wait_for_timeout(3000)

            # Buscar campo de documento
            await page.wait_for_selector("input[type='text'], input[name*='documento'], input[placeholder*='documento']", timeout=15000)
            campo = await page.query_selector("input[type='text'], input[name*='documento'], input[placeholder*='documento']")
            await campo.fill(cedula)

            # Buscar y hacer click en consultar
            btn = await page.query_selector("button[type='submit'], button:has-text('Consultar'), button:has-text('Buscar')")
            if btn:
                await btn.click()
            else:
                await page.keyboard.press("Enter")

            await page.wait_for_timeout(5000)

            resultado = {
                "cedula": cedula,
                "fuente": "RUNT",
                "tipo": "licencia_conduccion",
                "estado": "sin_datos",
                "nombre": "",
                "licencias": [],
                "restricciones": []
            }

            contenido = await page.content()

            if any(x in contenido.lower() for x in ["licencia", "conducción", "vigente", "vencida", "categoría"]):
                resultado["estado"] = "con_datos"

                # Extraer nombre
                nombre_el = await page.query_selector("[class*='nombre'], [class*='name'], h2, h3")
                if nombre_el:
                    resultado["nombre"] = (await nombre_el.inner_text()).strip()

                # Extraer tabla de licencias
                rows = await page.query_selector_all("table tbody tr")
                licencias = []
                for row in rows[:10]:
                    cells = await row.query_selector_all("td")
                    row_data = []
                    for cell in cells:
                        row_data.append((await cell.inner_text()).strip())
                    if any(row_data):
                        licencias.append(row_data)
                resultado["licencias"] = licencias

            elif any(x in contenido.lower() for x in ["no encontr", "no registra", "no existe"]):
                resultado["estado"] = "no_encontrado"

            return resultado

        except Exception as e:
            return {"cedula": cedula, "fuente": "RUNT", "estado": "error", "error": str(e)}
        finally:
            await browser.close()


# ─── RUNT VEHÍCULO ───────────────────────────────────────────────────────────
async def consultar_runt_vehiculo(placa: str) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        try:
            await page.goto(
                "https://portalpublico.runt.gov.co/#/consulta-vehiculo/consulta/consulta-ciudadana",
                timeout=30000
            )
            await page.wait_for_load_state("networkidle", timeout=20000)
            await page.wait_for_timeout(3000)

            await page.wait_for_selector("input[type='text'], input[name*='placa'], input[placeholder*='placa']", timeout=15000)
            campo = await page.query_selector("input[type='text'], input[name*='placa'], input[placeholder*='placa']")
            await campo.fill(placa.upper())

            btn = await page.query_selector("button[type='submit'], button:has-text('Consultar'), button:has-text('Buscar')")
            if btn:
                await btn.click()
            else:
                await page.keyboard.press("Enter")

            await page.wait_for_timeout(5000)

            resultado = {
                "placa": placa.upper(),
                "fuente": "RUNT",
                "tipo": "vehiculo",
                "estado": "sin_datos",
                "datos_vehiculo": {}
            }

            contenido = await page.content()

            if any(x in contenido.lower() for x in ["marca", "modelo", "cilindraje", "color", "propietario"]):
                resultado["estado"] = "con_datos"
                datos = {}

                # Extraer pares label/valor
                labels = await page.query_selector_all("[class*='label'], th, td:first-child")
                for lbl in labels[:30]:
                    txt = (await lbl.inner_text()).strip()
                    if txt and len(txt) < 50:
                        sibling = await lbl.evaluate_handle("el => el.nextElementSibling")
                        try:
                            val = await sibling.as_element().inner_text()
                            datos[txt] = val.strip()
                        except:
                            pass

                # Fallback: extraer toda la tabla
                rows = await page.query_selector_all("table tbody tr")
                for row in rows[:20]:
                    cells = await row.query_selector_all("td")
                    if len(cells) >= 2:
                        k = (await cells[0].inner_text()).strip()
                        v = (await cells[1].inner_text()).strip()
                        if k:
                            datos[k] = v

                resultado["datos_vehiculo"] = datos

            elif any(x in contenido.lower() for x in ["no encontr", "no registra", "no existe"]):
                resultado["estado"] = "no_encontrado"

            return resultado

        except Exception as e:
            return {"placa": placa, "fuente": "RUNT", "estado": "error", "error": str(e)}
        finally:
            await browser.close()


# ─── ENDPOINTS ───────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "ok", "service": "Consulta CO API"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/consultar")
async def consultar(req: ConsultaRequest):
    identificador = req.identificador.strip()
    if not identificador:
        raise HTTPException(status_code=400, detail="Identificador vacío")

    if req.tipo == "simit":
        return await consultar_simit(identificador)
    elif req.tipo == "runt_licencia":
        return await consultar_runt_licencia(identificador)
    elif req.tipo == "runt_vehiculo":
        return await consultar_runt_vehiculo(identificador)
    else:
        raise HTTPException(status_code=400, detail="Tipo inválido")

@app.post("/consultar/completo")
async def consultar_completo(req: ConsultaRequest):
    """Consulta SIMIT + RUNT licencia en paralelo para una cédula"""
    if req.tipo not in ["simit", "runt_licencia", "runt_vehiculo", "todos"]:
        raise HTTPException(status_code=400, detail="Tipo inválido")

    identificador = req.identificador.strip()

    if req.tipo == "todos":
        simit, runt = await asyncio.gather(
            consultar_simit(identificador),
            consultar_runt_licencia(identificador)
        )
        return {"simit": simit, "runt_licencia": runt}

    return await consultar(req)
