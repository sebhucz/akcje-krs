# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# KROK 1: Importowanie potrzebnych narzędzi (tzw. bibliotek)
# ---------------------------------------------------------------------------
import os
import requests
import time
from datetime import datetime, timedelta, timezone
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---------------------------------------------------------------------------
# KROK 2: Główna konfiguracja skryptu
# ---------------------------------------------------------------------------
EMAIL_NADAWCY = os.environ.get("EMAIL_SENDER")
HASLO_NADAWCY = os.environ.get("EMAIL_PASSWORD")
SERWER_SMTP = os.environ.get("SMTP_SERVER")
PORT_SMTP = os.environ.get("SMTP_PORT")

ADRES_BAZOWY_API = "https://api-krs.ms.gov.pl/api/krs"
OPÓŹNIENIE_API = 1
DNI_DO_SPRAWDZENIA = 30

# ---------------------------------------------------------------------------
# KROK 3: Definicje funkcji (główna logika skryptu)
# ---------------------------------------------------------------------------

def wczytaj_liste_krs_z_pliku(nazwa_pliku="krs_do_monitorowania.txt"):
    """Ta funkcja otwiera plik z listą numerów KRS i wczytuje je do pamięci."""
    try:
        with open(nazwa_pliku, 'r', encoding='utf-8') as plik:
            lista_krs = [linia.strip() for linia in plik if linia.strip()]
        print(f"📄 Wczytano {len(lista_krs)} numerów KRS z pliku '{nazwa_pliku}'.")
        return lista_krs
    except FileNotFoundError:
        print(f"❌ BŁĄD: Nie znaleziono pliku '{nazwa_pliku}'! Upewnij się, że plik istnieje w repozytorium.")
        return []

def wczytaj_odbiorcow_z_pliku(nazwa_pliku="odbiorcy.txt"):
    """Ta funkcja otwiera plik z listą adresów e-mail i wczytuje je do pamięci."""
    try:
        with open(nazwa_pliku, 'r', encoding='utf-8') as plik:
            odbiorcy = [linia.strip() for linia in plik if linia.strip() and '@' in linia]
        print(f"📧 Wczytano {len(odbiorcy)} odbiorców z pliku '{nazwa_pliku}'.\n")
        return odbiorcy
    except FileNotFoundError:
        print(f"❌ BŁĄD: Nie znaleziono pliku odbiorców '{nazwa_pliku}'! Upewnij się, że plik istnieje.")
        return []

def pobierz_pelny_odpis(numer_krs):
    """Ta funkcja wysyła do API prośbę o pełny odpis dla danego numeru KRS."""
    url = f"{ADRES_BAZOWY_API}/OdpisPelny/{numer_krs}?rejestr=P"
    try:
        odpowiedz = requests.get(url)
        if odpowiedz.status_code == 200:
            return odpowiedz.json()
    except requests.exceptions.RequestException:
        pass
    return None

# ✅ NOWA FUNKCJA ANALIZUJĄCA - DOKŁADNIE WEDŁUG TWOJEJ LOGIKI
def przeanalizuj_odpis_pod_katem_zmiany_kapitalu(odpis, data_poczatkowa, data_koncowa):
    """
    Analizuje odpis zgodnie z logiką: porównuje ostatni wpis w kapitale z ostatnim
    wpisem w całej historii spółki.
    """
    try:
        historia_wpisow = odpis.get('odpis', {}).get('naglowekP', {}).get('wpis', [])
        historia_kapitalu = odpis.get('dane', {}).get('dzial1', {}).get('kapital', {}).get('wysokoscKapitaluZakladowego', [])

        # Sprawdzamy, czy mamy wystarczająco danych do analizy
        if not historia_wpisow or not historia_kapitalu:
            return None

        # KROK 1: Znajdź ostatni wpis w historii kapitału (o najwyższym nrWpisuWprow)
        wpisy_kapitalu_wprowadzone = [k for k in historia_kapitalu if k.get('nrWpisuWprow')]
        if not wpisy_kapitalu_wprowadzone:
            return None
        ostatni_wpis_kapitalu = max(wpisy_kapitalu_wprowadzone, key=lambda k: int(k['nrWpisuWprow']))
        numer_ostatniego_wpisu_kapitalu = int(ostatni_wpis_kapitalu['nrWpisuWprow'])

        # KROK 2: Znajdź ostatni wpis w ogólnej historii spółki (o najwyższym numerWpisu)
        ostatni_wpis_ogolny = max(historia_wpisow, key=lambda w: int(w.get('numerWpisu', 0)))
        numer_ostatniego_wpisu_ogolnego = int(ostatni_wpis_ogolny.get('numerWpisu', 0))

        # KROK 3: Porównaj numery. Jeśli nie są równe, zakończ analizę.
        if numer_ostatniego_wpisu_kapitalu != numer_ostatniego_wpisu_ogolnego:
            # Ostatnia zmiana w spółce nie dotyczyła kapitału. Ignorujemy.
            return None
        
        # Jeśli numery się zgadzają, to znaczy, że ostatnia zmiana dotyczyła kapitału.
        # KROK 4: Sprawdź datę tej zmiany.
        data_zmiany = datetime.strptime(ostatni_wpis_ogolny['dataWpisu'], "%d.%m.%Y").date()
        if not (data_poczatkowa <= data_zmiany <= data_koncowa):
            # Zmiana jest poza naszym oknem czasowym. Ignorujemy.
            return None

        # Jeśli doszliśmy tutaj, to mamy pewność, że znaleziono zmianę kapitału
        # jako ostatnią operację w spółce i w zadanym czasie. Zbieramy dane do raportu.
        
        nowy_kapital = ostatni_wpis_kapitalu.get('wartosc')
        
        # Znajdź poprzedni kapitał (ten, który został wykreślony przez nasz wpis)
        wpis_poprzedniego_kapitalu = next((k for k in historia_kapitalu if k.get('nrWpisuWykr') and int(k.get('nrWpisuWykr')) == numer_ostatniego_wpisu_ogolnego), None)
        poprzedni_kapital = wpis_poprzedniego_kapitalu.get('wartosc') if wpis_poprzedniego_kapitalu else "Brak danych"
        
        # Znajdź aktualną nazwę
        historia_nazw = odpis.get('dane', {}).get('dzial1', {}).get('danePodmiotu', {}).get('nazwa', [])
        aktualna_nazwa_info = next((nazwa for nazwa in historia_nazw if 'nrWpisuWykr' not in nazwa), None)
        nazwa_firmy = aktualna_nazwa_info.get('nazwa') if aktualna_nazwa_info else "Nie udało się ustalić nazwy"

        return {
            "nazwa": nazwa_firmy,
            "krs": odpis.get('odpis', {}).get('naglowekP', {}).get('numerKRS'),
            "data_zmiany": ostatni_wpis_ogolny['dataWpisu'],
            "nowy_kapital": nowy_kapital,
            "poprzedni_kapital": poprzedni_kapital
        }

    except Exception as e:
        krs_dla_bledu = odpis.get('odpis', {}).get('naglowekP', {}).get('numerKRS', ' nieznany')
        print(f"   -> ⚠️ Wystąpił krytyczny błąd podczas analizy KRS {krs_dla_bledu}: {e}")
        return None

def wyslij_email(tresc_raportu, odbiorcy):
    """Ta funkcja jest odpowiedzialna za wysłanie gotowego raportu e-mailem."""
    if not odbiorcy:
        print("Brak zdefiniowanych odbiorców. Pomijam wysyłanie e-maila.")
        return
    if not all([EMAIL_NADAWCY, HASLO_NADAWCY, SERWER_SMTP, PORT_SMTP]):
        print("❌ BŁĄD: Brak pełnej konfiguracji e-mail. Sprawdź swoje sekrety na GitHubie.")
        return
    print(f"\n📧 Przygotowuję e-mail do wysłania do: {', '.join(odbiorcy)}...")
    wiadomosc = MIMEMultipart("alternative")
    wiadomosc["Subject"] = "Miesięczny raport zmian w kapitale zakładowym KRS"
    wiadomosc["From"] = EMAIL_NADAWCY
    wiadomosc["To"] = ", ".join(odbiorcy)
    wiadomosc.attach(MIMEText(tresc_raportu, "plain", "utf-8"))
    try:
        port = int(PORT_SMTP)
        kontekst_ssl = ssl.create_default_context()
        with smtplib.SMTP_SSL(SERWER_SMTP, port, context=kontekst_ssl) as serwer:
            serwer.login(EMAIL_NADAWCY, HASLO_NADAWCY)
            serwer.sendmail(EMAIL_NADAWCY, odbiorcy, wiadomosc.as_string())
        print("✅ E-mail został wysłany pomyślnie!")
    except Exception as e:
        print(f"❌ Wystąpił błąd podczas wysyłania e-maila: {e}")

# ---------------------------------------------------------------------------
# KROK 4: Główna funkcja wykonująca skrypt (uruchomienie)
# ---------------------------------------------------------------------------
def main():
    """Główna funkcja, która steruje całym procesem."""
    print("🚀 Start skryptu monitorującego zmiany w KRS.")
    
    lista_odbiorcow = wczytaj_odbiorcow_z_pliku()
    if not lista_odbiorcow:
        print("Brak zdefiniowanych odbiorców w pliku odbiorcy.txt. Kończę pracę.")
        return

    data_koncowa = datetime.now(timezone.utc).date()
    data_poczatkowa = data_koncowa - timedelta(days=DNI_DO_SPRAWDZENIA - 1)
    
    lista_krs_do_sprawdzenia = wczytaj_liste_krs_z_pliku()
    if not lista_krs_do_sprawdzenia:
        print("🏁 Lista KRS do sprawdzenia jest pusta. Koniec pracy.")
        return
        
    spolki_ze_zmiana_kapitalu = []
    liczba_spolek_do_sprawdzenia = len(lista_krs_do_sprawdzenia)
    
    for i, krs in enumerate(lista_krs_do_sprawdzenia, 1):
        print(f"🔎 Sprawdzam podmiot {i}/{liczba_spolek_do_sprawdzenia} (KRS: {krs})...")
        odpis = pobierz_pelny_odpis(krs)
        if odpis:
            informacje_o_zmianie = przeanalizuj_odpis_pod_katem_zmiany_kapitalu(odpis, data_poczatkowa, data_koncowa)
            if informacje_o_zmianie:
                print(f"   -> ⭐ ZNALEZIONO ZMIANĘ KAPITAŁU dla {informacje_o_zmianie['nazwa']}!")
                spolki_ze_zmiana_kapitalu.append(informacje_o_zmianie)
        time.sleep(OPÓŹNIENIE_API)

    if spolki_ze_zmiana_kapitalu:
        print(f"\n📊 Znaleziono {len(spolki_ze_zmiana_kapitalu)} spółek ze zmianą kapitału.")
        linie_raportu = [
            f"Raport zmian w kapitale zakładowym monitorowanych spółek w okresie od {data_poczatkowa.strftime('%d.%m.%Y')} do {data_koncowa.strftime('%d.%m.%Y')}.\n",
            f"Znaleziono {len(spolki_ze_zmiana_kapitalu)} podmiotów:\n",
            "--------------------------------------------------"
        ]
        for spolka in spolki_ze_zmiana_kapitalu:
            linia = (
                f"Nazwa: {spolka['nazwa']}\n"
                f"KRS: {spolka['krs']}\n"
                f"Data zmiany: {spolka['data_zmiany']}\n"
                f"Poprzedni kapitał: {spolka['poprzedni_kapital']} PLN\n"
                f"Nowy kapitał: {spolka['nowy_kapital']} PLN\n"
                "--------------------------------------------------"
            )
            linie_raportu.append(linia)
        tresc_raportu = "\n".join(linie_raportu)
        wyslij_email(tresc_raportu, lista_odbiorcow)
    else:
        print("\n✅ Na Twojej liście nie znaleziono żadnych spółek ze zmianą kapitału zakładowego w badanym okresie.")

    print("🏁 Skrypt zakończył pracę.")

if __name__ == "__main__":
    main()
