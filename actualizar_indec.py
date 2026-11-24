#!/usr/bin/env python3
"""
Plan B del tablero "Indicadores INDEC".

Baja de la API del INDEC los mismos datos que consulta el HTML y los guarda en
indec_datos.js, en esta misma carpeta. Al abrirse, el HTML levanta ese archivo
solo. Sirve si el navegador no puede consultar la API directo (por ejemplo, por
el proxy de la empresa).

Uso:
    python actualizar_indec.py

El token se toma de indec_config.js (en esta carpeta) o de la variable de
entorno INDEC_API_TOKEN. Solo usa la biblioteca estándar de Python 3.

También pasa construya.csv (el Índice Construya, que se carga a mano una vez
por mes) a construya_datos.js, que es el formato que lee el tablero.

Para que se actualice solo, programalo una vez por día después de las 16 h
(Programador de tareas en Windows, cron en Mac o Linux).
"""
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("INDEC_API_BASE", "https://api.indec.gob.ar/v1/")
AQUI = pathlib.Path(__file__).resolve().parent
DESTINO = AQUI / "indec_datos.js"


def leer_consultas():
    """Las consultas viven en el HTML, así el script y la página piden lo mismo."""
    for html in sorted(AQUI.glob("*.html")):
        texto = html.read_text(encoding="utf-8")
        m = re.search(r'<script type="application/json" id="consultas">(.*?)</script>', texto, re.S)
        if m:
            return json.loads(m.group(1))
    sys.exit("No encontré el HTML del tablero en esta carpeta.")


def leer_token():
    t = os.environ.get("INDEC_API_TOKEN", "").strip()
    if t:
        return t
    cfg = AQUI / "indec_config.js"
    if cfg.exists():
        m = re.search(r'INDEC_TOKEN\s*=\s*["\']([^"\']+)["\']', cfg.read_text(encoding="utf-8"))
        if m:
            return m.group(1).strip()
    sys.exit("Falta el token: ponelo en indec_config.js o en la variable INDEC_API_TOKEN.")


class ErrorToken(RuntimeError):
    pass


def pedir(url, token):
    for intento in range(1, 5):
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "User-Agent": "tablero-indicadores-indec",
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if (e.code == 429 or e.code >= 500) and intento < 4:
                time.sleep(1.5 * intento)
                continue
            detalle = e.read().decode("utf-8", "replace")[:300]
            if e.code in (401, 403):
                raise ErrorToken(f"El INDEC rechazó el token (HTTP {e.code})") from None
            raise RuntimeError(f"HTTP {e.code}: {detalle}") from None
        except urllib.error.URLError as e:
            if intento < 4:
                time.sleep(1.5 * intento)
                continue
            raise RuntimeError(f"No se pudo conectar: {e.reason}") from None
    raise RuntimeError("La API no respondió después de varios intentos")


def url_de(c, variante, offset):
    q = dict(variante)
    q.update(select=c["select"], order=c["order"], limit="1000", offset=str(offset))
    return BASE + c["path"] + "?" + "&".join(
        k + "=" + urllib.parse.quote(str(v), safe="*()") for k, v in q.items())


def bajar(c, token):
    ultimo = None
    for i, variante in enumerate(c["variantes"]):
        filas = []
        try:
            for pag in range(30):
                lote = pedir(url_de(c, variante, pag * 1000), token)
                if not isinstance(lote, list):
                    raise RuntimeError("respuesta con formato inesperado")
                filas.extend(lote)
                if len(lote) < 1000:
                    break
            if filas:
                return filas, i
            ultimo = "la consulta no devolvió filas"
        except ErrorToken:
            raise
        except RuntimeError as e:
            ultimo = str(e)
    raise RuntimeError(ultimo or "sin datos")


def leer_previo():
    if not DESTINO.exists():
        return {}
    m = re.search(r"window\.INDEC_SNAPSHOT\s*=\s*(\{.*\})\s*;?\s*$", DESTINO.read_text(encoding="utf-8"), re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


MESES_ES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
            "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}


def periodo_de(s):
    """Acepta 2026-10, 2026-10-01, 01/10/2026, oct-26, octubre 2026."""
    s = s.strip().strip('"').lower()
    m = re.match(r"^(\d{4})[-/](\d{1,2})(?:[-/]\d{1,2})?(?:[ t].*)?$", s)
    if m:
        y, mes = int(m[1]), int(m[2])
    else:
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
        if m:
            y, mes = int(m[3]), int(m[2])
        else:
            m = re.match(r"^([a-zé]+)[\s\-./]+(\d{2,4})$", s)
            if not m or m[1][:3] not in MESES_ES:
                return None
            y, mes = int(m[2]), MESES_ES[m[1][:3]]
    if y < 100:
        y += 2000
    return f"{y}-{mes:02d}" if 1 <= mes <= 12 else None


def numero(s):
    s = s.strip().strip('"').replace("%", "").replace("+", "").strip()
    if not s or s.upper() in ("N/A", "NA", "-", "—"):
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def construya():
    """Pasa construya.csv (cargado a mano) a construya_datos.js para el tablero."""
    src = AQUI / "construya.csv"
    if not src.exists():
        return
    try:
        texto = src.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        texto = src.read_text(encoding="latin-1")
    lineas = [l for l in texto.splitlines() if l.strip()]
    if len(lineas) < 2:
        print("AVISO construya.csv está vacío", file=sys.stderr)
        return
    sep = ";" if lineas[0].count(";") >= lineas[0].count(",") else ","
    filas, malas = {}, 0
    for l in lineas[1:]:
        c = l.split(sep) + [""] * 6
        p = periodo_de(c[0])
        if not p:
            malas += 1
            continue
        filas[p] = {"p": p, "idx": numero(c[1]), "ly": numero(c[2]), "acum": numero(c[3]),
                    "sa": numero(c[4]), "m": numero(c[5])}
    orden = [filas[p] for p in sorted(filas)]
    datos = {"filas": orden, "generado": int(time.time() * 1000)}
    (AQUI / "construya_datos.js").write_text(
        "window.CONSTRUYA = " + json.dumps(datos, separators=(",", ":")) + ";\n", encoding="utf-8")
    extra = f", {malas} fila(s) con período ilegible ignoradas" if malas else ""
    ultimo = orden[-1]["p"] if orden else "—"
    print(f"OK    Índice Construya: {len(orden)} meses, último {ultimo}{extra}")


def main():
    construya()
    consultas, token = leer_consultas(), leer_token()
    previo = leer_previo()
    datasets = dict(previo.get("datasets", {}))
    ahora = int(time.time() * 1000)
    errores = 0
    for c in consultas:
        try:
            filas, variante = bajar(c, token)
            datasets[c["id"]] = {"filas": filas, "at": ahora}
            extra = f" (consulta alternativa {variante + 1})" if variante else ""
            print(f"OK    {c['nombre']}: {len(filas)} filas{extra}")
        except ErrorToken as e:
            print(f"ERROR {e}. Generá un token nuevo en api.indec.gob.ar.", file=sys.stderr)
            errores += 1
            break
        except Exception as e:  # noqa: BLE001 - se informa y se sigue con las demás series
            errores += 1
            print(f"ERROR {c['nombre']}: {e}", file=sys.stderr)
    snap = {
        "datasets": datasets,
        "ultimaOk": ahora if errores == 0 else previo.get("ultimaOk", 0),
        "generado": ahora,
    }
    DESTINO.write_text("window.INDEC_SNAPSHOT = " + json.dumps(snap, ensure_ascii=False, separators=(",", ":")) + ";\n",
                       encoding="utf-8")
    print(("Listo" if errores == 0 else f"Terminado con {errores} error(es)") + f": {DESTINO}")
    sys.exit(1 if errores else 0)


if __name__ == "__main__":
    main()
