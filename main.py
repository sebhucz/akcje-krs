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
SERWER_SMTP = os.environ.get("SMTP_SERVER") # Odczytujemy z sekretów
PORT_SMTP = os.environ.get("SMTP_PORT")     # Odczytujemy z sekretów

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

def przeanalizuj_odpis_pod_katem_zmiany_kapitalu(odpis, data_poczatkowa, data_koncowa):
    """
    Analizuje odpis wg ostatecznej logiki: znajduje najnowszą zmianę w historii kapitału
    i na tej podstawie weryfikuje datę.
    """
    try:
        historia_wpisow = odpis.get('odpis', {}).get('naglowekP', {}).get('wpis', [])
        dane_dzial1 = odpis.get('dane', {}).get('dzial1', {})
        historia_kapitalu = dane_dzial1.get('kapital', {}).get('wysokoscKapitaluZakladowego', [])
        
        if not historia_kapitalu or len(historia_kapitalu) < 2:
            return None

        wpisy_wprowadzone = [k for k in historia_kapitalu if 'nrWpisuWprow' in k]
        if not wpisy_wprowadzone:
            return None
            
        ostatnia_zmiana_kapitalu = max(wpisy_wprowadzone, key=lambda k: int(k.get('nrWpisuWprow', 0)))
        numer_wpisu_zmieniajacego = int(ostatnia_zmiana_kapitalu.get('nrWpisuWprow', 0))

        if numer_wpisu_zmieniajacego == 0:
            return None

        wpis_sadowy = next((w for w in historia_wpisow if int(w.get('numerWpisu', -1)) == numer_wpisu_zmieniajacego), None)
        if not wpis_sadowy:
            return None

        data_zmiany = datetime.strptime(wpis_sadowy['dataWpisu'], "%d.%m.%Y").date()

        if not (data_poczatkowa <= data_zmiany <= data_koncowa):
            return None
        
        nowy_kapital = ostatnia_zmiana_kapitalu.get('wartosc', 'Brak danych')

        starsze_kapitaly = [k for k in wpisy_wprowadzone if int(k.get('nrWpisuWprow', 0)) < numer_wpisu_zmieniajacego]
        poprzedni_kapital = "Brak danych"
        if starsze_kapitaly:
            ostatni_poprzedni_kapital = sorted(starsze_kapitaly, key=lambda k: int(k.get('nrWpisuWprow', 0)), reverse=True)[0]
            poprzedni_kapital = ostatni_poprzedni_kapital.get('wartosc', 'Brak danych')

        historia_nazw = dane_dzial1.get('danePodmiotu', {}).get('nazwa', [])
        aktualna_nazwa_info = next((nazwa for nazwa in historia_nazw if 'nrWpisuWykr' not in nazwa), None)
        nazwa_firmy = aktualna_nazwa_info['nazwa'] if aktualna_nazwa_info else "Nie udało się ustalić nazwy"

        return {
            "nazwa": nazwa_firmy,
            "krs": odpis.get('odpis', {}).get('naglowekP', {}).get('numerKRS'),
            "data_zmiany": wpis_sadowy['dataWpisu'],
            "nowy_kapital": nowy_kapital,
            "poprzedni_kapital": poprzedni_kapital
        }

    except (KeyError, IndexError, TypeError, ValueError) as e:
        krs_dla_bledu = odpis.get('odpis', {}).get('naglowekP', {}).get('numerKRS', ' nieznany')
        print(f"   -> ⚠️ Wystąpił krytyczny błąd podczas analizy KRS {krs_dla_bledu}: {e}")
        return None
    return None

def wyslij_email(tresc_raportu, odbiorcy):
    """Ta funkcja jest odpowiedzialna za wysłanie gotowego raportu e-mailem."""
    if not odbiorcy:
        print("Brak zdefiniowanych odbiorców. Pomijam wysyłanie e-maila.")
        return
    
    # Sprawdzamy, czy wszystkie sekrety zostały wczytane
    if not all([EMAIL_NADAWCY, HASLO_NADAWCY, SERWER_SMTP, PORT_SMTP]):
        print("❌ BŁĄD: Brak pełnej konfiguracji e-mail. Sprawdź swoje sekrety na GitHubie (EMAIL_SENDER, EMAIL_PASSWORD, SMTP_SERVER, SMTP_PORT).")
        print("Wiadomość nie została wysłana.")
        return
        
    print(f"\n📧 Przygotowuję e-mail do wysłania do: {', '.join(odbiorcy)}...")
    
    wiadomosc = MIMEMultipart("alternative")
    wiadomosc["Subject"] = "Miesięczny raport zmian w kapitale zakładowym KRS"
    wiadomosc["From"] = EMAIL_NADAWCY
    wiadomosc["To"] = ", ".join(odbiorcy)
    wiadomosc.attach(MIMEText(tresc_raportu, "plain", "utf-8"))
    
    try:
        # Konwertujemy port na liczbę całkowitą
        port = int(PORT_SMTP)
        kontekst_ssl = ssl.create_default_context()
        
        # Używamy bezpiecznego połączenia SMTP_SSL, idealnego dla Gmaila na porcie 465
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
