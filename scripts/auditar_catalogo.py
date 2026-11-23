#!/usr/bin/env python3
"""Auditoría anual del catálogo de «Herrajes en proyecto».

Lee el bloque CATALOGO_DB de herrajes-en-proyecto.html, abre la ficha web de cada
referencia (hafele.es y verduonlinestore.com) y genera informe_vigencia_catalogo.md
con las que hay que revisar al empezar el curso.

Uso (desde la carpeta del repositorio):
    python scripts/auditar_catalogo.py                       # todas las referencias
    python scripts/auditar_catalogo.py --solo Verdú          # un proveedor
    python scripts/auditar_catalogo.py --html otra/ruta.html --salida informe.md --pausa 1.5

Solo usa la biblioteca estándar de Python. No envía datos personales: hace peticiones
GET anónimas con una cabecera de navegador genérica.
"""
import argparse, datetime, gzip, html, json, os, re, sys, time, urllib.error, urllib.request, zlib

AQUI = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(AQUI)
CABECERAS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'es-ES,es;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
}
# Textos que indican que el artículo ya no se vende (cuentan aunque la referencia aparezca)
FUERTES = ['descatalogado', 'ya no está disponible', 'ya no esta disponible', 'fuera de catálogo', 'artículo no disponible']
# Textos que solo cuentan si la referencia no aparece en la página
AVISOS = [
    'descatalogado', 'ya no está disponible', 'ya no esta disponible', 'artículo no disponible', 'no disponible actualmente',
    'producto no encontrado', 'página no encontrada', 'pagina no encontrada', 'no se ha encontrado', 'sin resultados',
    'no hay resultados', 'error 404', 'esta página no existe', 'agotado', 'fuera de catálogo',
]


def lee_catalogo(ruta):
    txt = open(ruta, encoding='utf-8').read()
    m = re.search(r'<script type="application/json" id="CATALOGO_DB">\s*(.*?)\s*</script>', txt, re.S)
    if not m:
        sys.exit('No encuentro el bloque CATALOGO_DB en ' + ruta)
    return json.loads(m.group(1).replace('<\\/', '</'))


def descarga(url, timeout=25):
    req = urllib.request.Request(url, headers=CABECERAS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            datos, cod, final = r.read(), r.status, r.geturl()
            enc = (r.headers.get('Content-Encoding') or '').lower()
    except urllib.error.HTTPError as e:
        return e.code, '', url
    except Exception as e:  # red, certificado, tiempo de espera…
        return None, str(e), url
    if enc == 'gzip':
        datos = gzip.decompress(datos)
    elif enc == 'deflate':
        datos = zlib.decompress(datos)
    return cod, datos.decode('utf-8', 'replace'), final


def busca_chrome():
    """Chrome o Edge instalados: se usan sin ventana cuando la web bloquea la petición directa (Häfele responde 403)."""
    cands = [os.environ.get('CHROME', ''),
             r'C:\Program Files\Google\Chrome\Application\chrome.exe', r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
             r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', '/usr/bin/google-chrome', '/usr/bin/chromium',
             '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
    return next((c for c in cands if c and os.path.exists(c)), None)


CHROME = busca_chrome()


def descarga_chrome(url, timeout=70):
    import subprocess
    if not CHROME:
        return None, 'no hay Chrome ni Edge instalado', url
    try:
        r = subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--virtual-time-budget=8000',
                            '--user-agent=' + CABECERAS['User-Agent'], '--dump-dom', url],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
        return (200 if len(r.stdout) > 2000 else None), r.stdout, url
    except Exception as e:
        return None, str(e), url


def texto_plano(h):
    h = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', h, flags=re.S | re.I)
    return html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', h))).lower()


def revisa(a, pausa):
    """Devuelve (estado, detalle). Estados: VIGENTE, REVISAR, NO_ENCONTRADA, SIN_RESPUESTA."""
    url, ref = a['url_ficha'], a['ref']
    cod, cuerpo, final = descarga(url)
    if cod in (401, 403, 429, None) or (cod == 200 and len(cuerpo) < 3000):
        cod, cuerpo, final = descarga_chrome(url)   # la web bloquea las peticiones directas
    time.sleep(pausa)
    if cod is None:
        return 'SIN_RESPUESTA', f'sin conexión: {cuerpo[:80]}'
    if cod in (404, 410):
        return 'NO_ENCONTRADA', f'HTTP {cod}'
    if cod >= 400:
        return 'SIN_RESPUESTA', f'HTTP {cod} (la web puede bloquear peticiones automáticas: abrir a mano)'
    t = texto_plano(cuerpo)
    ref_sin = ref.replace('.', '')
    aparece = ref.lower() in t or ref_sin in t.replace('.', '').replace(' ', '')
    fuerte = next((x for x in FUERTES if x in t), None)
    aviso = fuerte or (None if aparece else next((x for x in AVISOS if x in t), None))
    p = a.get('parametros_tecnicos', {}).get('P')
    if p and p.lower() not in (final.lower() + t):
        aviso = aviso or f'la ficha {p} ya no aparece'
    if aviso and not aparece:
        return 'NO_ENCONTRADA', f'aviso en la página: «{aviso}» y la referencia no aparece'
    if aviso:
        return 'REVISAR', f'aviso en la página: «{aviso}»'
    if not aparece:
        return 'REVISAR', 'la página responde, pero la referencia no aparece en el texto (puede ir en una tabla cargada después: abrir a mano)'
    if final.rstrip('/') != url.rstrip('/') and 'SearchTerm' not in url:
        return 'REVISAR', f'la ficha redirige a {final}'
    return 'VIGENTE', 'la referencia aparece en la ficha'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--html', default=os.path.join(REPO, 'herrajes-en-proyecto.html'))
    ap.add_argument('--salida', default=os.path.join(REPO, 'informe_vigencia_catalogo.md'))
    ap.add_argument('--solo', help='Häfele o Verdú')
    ap.add_argument('--pausa', type=float, default=1.0, help='segundos entre peticiones (no saturar las webs)')
    a = ap.parse_args()
    db = lee_catalogo(a.html)
    arts = [x for x in db['articulos'] if not a.solo or x['proveedor'].lower() == a.solo.lower()]
    # una petición por URL distinta: varias referencias comparten ficha
    por_url, res = {}, []
    for i, art in enumerate(arts, 1):
        print(f'[{i}/{len(arts)}] {art["proveedor"]} {art["ref"]}', end=' … ', flush=True)
        clave = (art['url_ficha'], art['ref'])
        if art['url_ficha'] in por_url and por_url[art['url_ficha']][0] == 'SIN_RESPUESTA':
            est = por_url[art['url_ficha']]
        else:
            est = revisa(art, a.pausa)
            por_url[art['url_ficha']] = est
        print(est[0])
        res.append((art, *est))
    orden = {'NO_ENCONTRADA': 0, 'REVISAR': 1, 'SIN_RESPUESTA': 2, 'VIGENTE': 3}
    res.sort(key=lambda r: (orden[r[1]], r[0]['familia'], r[0]['ref']))
    hoy = datetime.date.today().strftime('%d/%m/%Y')
    cuenta = {k: sum(1 for r in res if r[1] == k) for k in orden}
    L = [f'# Informe de vigencia del catálogo · Herrajes en proyecto', '',
         f'- Fecha de la auditoría: {hoy}',
         f'- Curso del catálogo en el juego: **{db.get("curso")}** (última verificación: {db.get("verificado")})',
         f'- Referencias revisadas: {len(res)} · no encontradas: **{cuenta["NO_ENCONTRADA"]}** · a revisar: **{cuenta["REVISAR"]}** · sin respuesta: {cuenta["SIN_RESPUESTA"]} · vigentes: {cuenta["VIGENTE"]}', '',
         'Qué hacer: sigue el protocolo de la habilidad «actualizar-catalogo-herrajes». Las «sin respuesta» suelen ser bloqueos de la web a peticiones automáticas: ábrelas en el navegador.', '']
    for est, tit in [('NO_ENCONTRADA', 'Referencias que hay que sustituir'), ('REVISAR', 'Referencias que hay que revisar a mano'),
                     ('SIN_RESPUESTA', 'Fichas que no respondieron'), ('VIGENTE', 'Referencias vigentes')]:
        filas = [r for r in res if r[1] == est]
        if not filas:
            continue
        L += [f'## {tit} ({len(filas)})', '', '| Fam. | Proveedor | Ref. | Artículo | Detalle | Ficha |', '|---|---|---|---|---|---|']
        for art, _, det in filas:
            nom = art['nombre'].replace('|', '/')
            L.append(f'| {art["familia"]} | {art["proveedor"]} | `{art["ref"]}` | {nom} | {det} | [abrir]({art["url_ficha"]}) |')
        L.append('')
    open(a.salida, 'w', encoding='utf-8').write('\n'.join(L))
    print('\nInforme:', a.salida)


if __name__ == '__main__':
    main()
