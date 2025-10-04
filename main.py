# -*- coding: utf-8 -*-
"""
Skrypt: Monitor zmian kapitału zakładowego w KRS (okno: ostatnie 30 dni)
Autor: (ja 😊)

Co robi?
1) Wczytuje listę numerów KRS z pliku 'krs_do_monitorowania.txt'.
2) Dla każdego KRS pobiera publiczny odpis JSON z API KRS.
3) Analizuje pełny odpis: wyszukuje WSZYSTKIE zmiany kapitału zakładowego,
   które zostały wprowadzone w ostatnich 30 dniach (liczone wg daty wpisu).
4) Jeśli znajdzie jakiekolwiek zmiany – buduje zbiorczy raport i wysyła go e-mailem
   do adresów z pliku 'odbiorcy.txt'.
5) Czytelnie loguje przebieg działania (co sprawdza, co znalazł itp.).

Wymagane biblioteki: requests

Uwaga o datach:
- API KRS zwraca daty wpisów w formacie DD.MM.RRRR (np. "16.09.2025").
- Okno "ostatnie 30 dni" liczymy względem daty DZISIAJ w strefie Europe/Warsaw.
"""

import os
import sys
import ssl
import time
import smtplib
import requests
from datetime import datetime, timedelta, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Od Python 3.9 mamy wbudowaną strefę czasową:
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Warsaw")
except Exception:
    TZ = None  # jeśli system nie ma zoneinfo, zadziałamy "na sucho" z datą lokalną

# ------------------------------
# KONFIGURACJA UŻYTKOWA (zmień w razie potrzeby)
# ------------------------------
DNI_OKNA = 30
PLIK_KRS = "krs_do_monitorowania.txt"
PLIK_ODB = "odbiorcy.txt"
API_ODPIS_URL = "https://api-krs.ms.gov.pl/api/krs/OdpisPelny/{krs}"  # {krs} podstawiamy numerem
REQUEST_TIMEOUT = 15  # sekundy

# SMTP: podaj przez zmienne środowiskowe (to bezpieczniejsze niż wpisywanie do pliku)
# Przykład (w PowerShell / Bash):
#   setx SMTP_HOST "smtp.twojserwer.pl"
#   setx SMTP_PORT "465"
#   setx SMTP_USER "noreply@twojadomena.pl"
#   setx SMTP_PASS "tajnehaslo"
#   setx EMAIL_FROM "noreply@twojadomena.pl"
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465") or 465)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER or "")
EMAIL_SUBJECT = "Ostatnie rejestracje akcji spółek publicznych"

# ------------------------------
# NARZĘDZIA POMOCNICZE
# ------------------------------
def dzis_w_warszawie() -> date:
    """Zwraca dzisiejszą datę w strefie Europe/Warsaw (jeśli możliwe)."""
    if TZ:
        return datetime.now(TZ).date()
    return datetime.now().date()  # fallback

def parse_pl_date(s: str) -> date:
    """Parsuje 'DD.MM.RRRR' na obiekt datetime.date."""
    return datetime.strptime(s, "%d.%m.%Y").date()

def wczytaj_linie_z_pliku(nazwa_pliku: str) -> list[str]:
    """
    Wczytuje linie z pliku, usuwa białe znaki, pomija puste.
    Jeśli plik nie istnieje – zwraca pustą listę i loguje błąd.
    """
    try:
        with open(nazwa_pliku, "r", encoding="utf-8") as f:
            return [linia.strip() for linia in f if linia.strip()]
    except FileNotFoundError:
        print(f"❌ BŁĄD: Nie znaleziono pliku '{nazwa_pliku}'. Upewnij się, że plik jest obok skryptu.")
        return []

def wczytaj_krs_z_pliku(nazwa_pliku: str = PLIK_KRS) -> list[str]:
    """Wczytuje numery KRS (jeden na linię)."""
    krsy = wczytaj_linie_z_pliku(nazwa_pliku)
    print(f"📄 Wczytano {len(krsy)} numerów KRS z pliku '{nazwa_pliku}'.")
    return krsy

def wczytaj_odbiorcow_z_pliku(nazwa_pliku: str = PLIK_ODB) -> list[str]:
    """Wczytuje adresy e-mail odbiorców (jeden na linię, musi zawierać '@')."""
    odb = [linia for linia in wczytaj_linie_z_pliku(nazwa_pliku) if "@" in linia]
    print(f"📧 Wczytano {len(odb)} odbiorców z pliku '{nazwa_pliku}'.")
    return odb

def pobierz_pelny_odpis_json(krs: str) -> dict | None:
    """
    Pobiera pełny odpis JSON dla podanego KRS.
    Zwraca dict (parsowany JSON) lub None w razie błędu.
    """
    url = API_ODPIS_URL.format(krs=krs)
    try:
        resp = requests.get(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "krs-monitor/1.0 (mailto:{})".format(EMAIL_FROM or "unknown")
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"   -> ⚠️ API zwróciło status {resp.status_code} dla KRS {krs}.")
            return None

        data = resp.json()
        # Niektóre odpowiedzi mają dane w polu 'odpis'; interesuje nas właśnie to drzewo.
        if not isinstance(data, dict) or "odpis" not in data:
            print("   -> ⚠️ Nie znaleziono klucza 'odpis' w odpowiedzi API – pomijam.")
            return None
        return data

    except requests.exceptions.Timeout:
        print(f"   -> ⚠️ Przekroczono timeout {REQUEST_TIMEOUT}s dla KRS {krs}.")
    except Exception as e:
        print(f"   -> ⚠️ Błąd pobierania dla KRS {krs}: {e}")
    return None

# ------------------------------
# ANALIZA: wyszukiwanie zmian kapitału w oknie 30 dni
# ------------------------------
def znajdz_zmiany_kapitalu_w_oknie(odpis: dict, data_od: date, data_do: date) -> list[dict]:
    """
    Zwraca listę WSZYSTKICH zmian kapitału w oknie [data_od, data_do] (obie granice włącznie).
    Każdy element listy ma pola:
      - nazwa
      - krs
      - data_zmiany (DD.MM.RRRR – data wpisu)
      - nowy_kapital
      - poprzedni_kapital (lub None, jeśli nie da się ustalić)

    Jak działa (krótko i po ludzku):
    1) Budujemy słownik: numerWpisu -> wpis (żeby po numerze szybko znaleźć datę wpisu).
    2) Przechodzimy po wszystkich pozycjach „kapitału zakładowego”.
       Każda pozycja ma informację, w którym wpisie została WPROWADZONA (nrWpisuWprow).
    3) Sprawdzamy datę tego wpisu – jeśli mieści się w naszym oknie, traktujemy to jako „zmianę”.
    4) „Poprzednią” wartość bierzemy z pozycji, której nrWpisuWykr == nrWpisuWprow (tak działa wersjonowanie w KRS).
    """
    wyniki: list[dict] = []
    try:
        naglowek = odpis.get("odpis", {}).get("naglowekP", {})
        wszystkie_wpisy = naglowek.get("wpis", []) or []
        mapa_wpisow = {}
        for w in wszystkie_wpisy:
            try:
                nr = int(w.get("numerWpisu"))
                mapa_wpisow[nr] = w
            except Exception:
                continue

        # Historia kapitału – UWAŻAJ: poprawna ścieżka jest „głębiej”:
        historia_kapitalu = (
            odpis.get("odpis", {})
                 .get("dane", {})
                 .get("dzial1", {})
                 .get("kapital", {})
                 .get("wysokoscKapitaluZakladowego", [])
        )
        if not historia_kapitalu:
            print("   -> brak sekcji 'kapital/wysokoscKapitaluZakladowego' – pomijam.")
            return []

        # Bieżąca nazwa spółki = pozycja NAJNOWSZA bez 'nrWpisuWykr'
        historia_nazw = (
            odpis.get("odpis", {})
                 .get("dane", {})
                 .get("dzial1", {})
                 .get("danePodmiotu", {})
                 .get("nazwa", [])
        )
        aktualna_nazwa = next((n for n in historia_nazw if "nrWpisuWykr" not in n), None)
        nazwa_firmy = (aktualna_nazwa or {}).get("nazwa", "Nie udało się ustalić nazwy")
        krs = naglowek.get("numerKRS", "????")

        # Iterujemy po pozycjach kapitału i wyławiamy te, które „weszły” w oknie czasu
        for pozycja in historia_kapitalu:
            nr_wprow = pozycja.get("nrWpisuWprow")
            if not nr_wprow:
                continue  # pozycje bez numeru wprowadzającego nas nie interesują

            try:
                nr = int(nr_wprow)
            except ValueError:
                continue

            wpis = mapa_wpisow.get(nr)
            if not wpis or "dataWpisu" not in wpis:
                continue

            try:
                data_wpisu = parse_pl_date(wpis["dataWpisu"])
            except Exception:
                continue

            if not (data_od <= data_wpisu <= data_do):
                continue  # poza badanym oknem

            nowy_kapital = pozycja.get("wartosc")

            # „Poprzednia” wartość – pozycja, której nrWpisuWykr == nasz nr_wprow
            poprzednie = [x for x in historia_kapitalu if x.get("nrWpisuWykr") == str(nr)]
            poprzedni_kapital = poprzednie[0].get("wartosc") if poprzednie else None

            wyniki.append({
                "nazwa": nazwa_firmy,
                "krs": krs,
                "data_zmiany": wpis["dataWpisu"],   # DD.MM.RRRR
                "nowy_kapital": nowy_kapital,
                "poprzedni_kapital": poprzedni_kapital,
            })

        # Sortujemy malejąco po dacie zmiany (najnowsze na górze)
        wyniki.sort(
            key=lambda z: datetime.strptime(z["data_zmiany"], "%d.%m.%Y"),
            reverse=True
        )
        if not wyniki:
            print("   -> są wpisy o kapitale, ale żaden nie mieści się w oknie 30 dni.")
        return wyniki

    except Exception as e:
        krs_bledu = odpis.get("odpis", {}).get("naglowekP", {}).get("numerKRS", "nieznany")
        print(f"   -> ⚠️ Wystąpił błąd podczas analizy KRS {krs_bledu}: {e}")
        return []

# ------------------------------
# BUDOWANIE RAPORTU TEKSTOWEGO
# ------------------------------
def zbuduj_tresc_maila_html(zmiany: list[dict]) -> str:
    """
    Buduje estetyczną wiadomość HTML:
    - nagłówek wg wymagań,
    - krótki wstęp,
    - tabelę z wynikami (wyróżniona nazwa spółki).
    """
    if not zmiany:
        return """
        <html><body style="font-family: Arial, Helvetica, sans-serif; color:#111;">
          <p>Cześć, to ja, Wasz skrypt (made by SH&AI) monitorujący zmiany w kapitale zakładowym w spółkach publicznych.</p>
          <p>W badanym okresie nie odnotowano zmian kapitału zakładowego.</p>
        </body></html>
        """

    # Prosty, bezpieczny w HTML e-mailach CSS (inline + style w <table>)
    rows = []
    for z in zmiany:
        rows.append(f"""
          <tr>
            <td style="padding:10px 12px; border-bottom:1px solid #e9ecef;"><strong>{z['nazwa']}</strong></td>
            <td style="padding:10px 12px; border-bottom:1px solid #e9ecef;">{z['krs']}</td>
            <td style="padding:10px 12px; border-bottom:1px solid #e9ecef; text-align:right;">{z.get('poprzedni_kapital', '–')}</td>
            <td style="padding:10px 12px; border-bottom:1px solid #e9ecef; text-align:right;">{z['nowy_kapital']}</td>
            <td style="padding:10px 12px; border-bottom:1px solid #e9ecef; white-space:nowrap;">{z['data_zmiany']}</td>
          </tr>
        """)

    html = f"""
    <html>
      <body style="font-family: Arial, Helvetica, sans-serif; color:#111; line-height:1.45;">
        <h2 style="margin:0 0 12px 0;">Ostatnie rejestracje akcji spółek publicznych</h2>

        <p>Cześć, to ja, Wasz skrypt (made by SH&AI) monitorujący zmiany w kapitale zakładowym w spółkach publicznych.</p>
        <p>Wykryto zmiany kapitału zakładowego w badanym okresie dla:</p>

        <table role="presentation" cellpadding="0" cellspacing="0" border="0"
               style="border-collapse:collapse; width:100%; max-width:920px; background:#fff; border:1px solid #e9ecef;">
          <thead>
            <tr style="background:#f8f9fa;">
              <th align="left"  style="padding:10px 12px; border-bottom:1px solid #e9ecef; font-weight:600;">Spółka</th>
              <th align="left"  style="padding:10px 12px; border-bottom:1px solid #e9ecef; font-weight:600;">KRS</th>
              <th align="right" style="padding:10px 12px; border-bottom:1px solid #e9ecef; font-weight:600;">Poprzedni kapitał</th>
              <th align="right" style="padding:10px 12px; border-bottom:1px solid #e9ecef; font-weight:600;">Nowy kapitał</th>
              <th align="left"  style="padding:10px 12px; border-bottom:1px solid #e9ecef; font-weight:600;">Data zmiany</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows)}
          </tbody>
        </table>

        <p style="margin-top:16px; color:#555; font-size:12px;">
          Wiadomość wygenerowana automatycznie – proszę nie odpowiadać.
        </p>
      </body>
    </html>
    """
    return html


def zbuduj_tresc_maila_text(zmiany: list[dict]) -> str:
    """
    Tekstowy fallback (gdy klient pocztowy nie wyświetla HTML).
    Zostawia tę samą strukturę logiczną, ale bez tabeli.
    """
    if not zmiany:
        return (
            "Cześć, to ja, skrypt monitorujący zmiany w kapitale zakładowym w spółkach publicznych.\n\n"
            "W badanym okresie nie odnotowano zmian kapitału zakładowego."
        )

    linie = [
        "Ostatnie rejestracje akcji spółek publicznych",
        "",
        "Cześć, to ja, skrypt monitorujący zmiany w kapitale zakładowym w spółkach publicznych.",
        "",
        "Wykryto zmiany kapitału zakładowego w badanym okresie dla:",
        ""
    ]
    for z in zmiany:
        linie.append(
            f"- {z['nazwa']} (KRS {z['krs']}): "
            f"{z.get('poprzedni_kapital','–')} -> {z['nowy_kapital']} "
            f"dnia {z['data_zmiany']}"
        )
    return "\n".join(linie)


# ------------------------------
# WYSYŁKA E-MAIL
# ------------------------------
def wyslij_email_do_odbiorcow(tresc_html: str, tresc_text: str, odbiorcy: list[str]) -> bool:
    """
    Wysyła e-mail jako multipart/alternative:
      - część 'plain text' (fallback),
      - część 'text/html' (docelowa, z tabelą).
    """
    if not odbiorcy:
        print("   -> ⚠️ Brak odbiorców – nie wysyłam e-maila.")
        return False
    if not (SMTP_HOST and SMTP_PORT and SMTP_USER and SMTP_PASS and EMAIL_FROM):
        print("   -> ⚠️ Brak kompletu zmiennych SMTP – nie wysyłam e-maila.")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(odbiorcy)
    msg["Subject"] = EMAIL_SUBJECT

    # Najpierw plain text (musi być pierwszy), potem HTML
    part_text = MIMEText(tresc_text, "plain", "utf-8")
    part_html = MIMEText(tresc_html, "html", "utf-8")
    msg.attach(part_text)
    msg.attach(part_html)

    try:
        if SMTP_PORT == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(EMAIL_FROM, odbiorcy, msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(EMAIL_FROM, odbiorcy, msg.as_string())

        print(f"📨 Wysłano e-mail do {len(odbiorcy)} odbiorców.")
        return True
    except Exception as e:
        print(f"❌ Błąd wysyłki e-mail: {e}")
        return False


# ------------------------------
# GŁÓWNY PRZEBIEG
# ------------------------------
def main():
    print("🚀 Start skryptu monitorującego zmiany w KRS.")

    odbiorcy = wczytaj_odbiorcow_z_pliku(PLIK_ODB)
    krs_lista = wczytaj_krs_z_pliku(PLIK_KRS)

    # Ustalamy okno dat: [dzis - 30 dni, dzis]
    dzis = dzis_w_warszawie()
    data_od = dzis - timedelta(days=DNI_OKNA)
    data_do = dzis
    print(f"🗓️ Okno analizowane: {data_od.strftime('%d.%m.%Y')} – {data_do.strftime('%d.%m.%Y')}")

    # >>> WAŻNE: inicjalizacja PRZED pętlą <<<
    wszystkie_zmiany: list[dict] = []

    for i, krs in enumerate(krs_lista, start=1):
        print(f"🔎 Sprawdzam podmiot {i}/{len(krs_lista)} (KRS: {krs})...")
        odpis = pobierz_pelny_odpis_json(krs)
        if not odpis:
            continue

        zmiany = znajdz_zmiany_kapitalu_w_oknie(odpis, data_od, data_do)
        if zmiany:
            print(f"   -> ✅ Znaleziono {len(zmiany)} zmian(y) w oknie czasu.")
            wszystkie_zmiany.extend(zmiany)
        else:
            print("   -> ℹ️ Brak zmian kapitału w oknie czasu.")

        # mały odstęp, żeby nie przeciążać API
        time.sleep(0.4)

    # >>> Ten blok MUSI być w funkcji main(), po pętli, gdy 'wszystkie_zmiany' już istnieje <<<
    if wszystkie_zmiany:
        tresc_html = zbuduj_tresc_maila_html(wszystkie_zmiany)
        tresc_text = zbuduj_tresc_maila_text(wszystkie_zmiany)

        print("\n📋 Podsumowanie zmian (tekst):\n" + tresc_text + "\n")
        wyslij_email_do_odbiorcow(tresc_html, tresc_text, odbiorcy)
    else:
        print("\n✅ Na Twojej liście nie znaleziono żadnych spółek ze zmianą kapitału zakładowego w badanym okresie.")

    print("🏁 Skrypt zakończył pracę.")

# Uruchom tylko wtedy, gdy plik jest odpalany bezpośrednio (a nie importowany jako moduł)
if __name__ == "__main__":
    main()
