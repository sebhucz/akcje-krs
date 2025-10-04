# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# KROK 1: Importowanie potrzebnych narzędzi (tzw. bibliotek)
# ---------------------------------------------------------------------------
# Każda biblioteka to zestaw gotowych narzędzi do określonych zadań.

# Biblioteka 'os' pozwala na dostęp do systemu operacyjnego,
# w naszym przypadku używamy jej do bezpiecznego odczytywania danych
# konfiguracyjnych (tzw. "sekretów") na GitHubie.
import os

# Biblioteka 'requests' to nasze narzędzie do wysyłania zapytań
# przez internet, czyli do komunikacji z API KRS.
import requests

# Biblioteka 'time' pozwala na zarządzanie czasem, np. na wstrzymywanie
# działania skryptu na określoną liczbę sekund.
import time

# Biblioteki 'datetime' i 'timedelta' służą do pracy z datami i czasem.
# Używamy ich, aby obliczyć zakres dat do sprawdzenia (np. ostatnie 30 dni).
from datetime import datetime, timedelta, timezone

# Te biblioteki są potrzebne do tworzenia i wysyłania wiadomości e-mail.
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---------------------------------------------------------------------------
# KROK 2: Główna konfiguracja skryptu
# ---------------------------------------------------------------------------
# W tej sekcji definiujemy stałe wartości, z których skrypt będzie korzystał.

# Odczytywanie konfiguracji e-mail z sekretów na GitHubie.
# To bezpieczny sposób na przechowywanie haseł - nie są one widoczne w kodzie.
EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 465))

# Ustawienia dotyczące API KRS
ADRES_BAZOWY_API = "https://api-krs.ms.gov.pl/api/krs"
# Opóźnienie w sekundach między kolejnymi zapytaniami do serwera.
# Zapobiega to zablokowaniu naszego skryptu za zbyt częste odpytywanie.
OPÓŹNIENIE_API = 1

# Ustawienie, ile dni wstecz skrypt ma sprawdzać zmiany.
DNI_DO_SPRAWDZENIA = 30

# ---------------------------------------------------------------------------
# KROK 3: Definicje funkcji (główna logika skryptu)
# ---------------------------------------------------------------------------
# Dzielimy kod na funkcje, aby był bardziej czytelny. Każda funkcja ma jedno zadanie.

def wczytaj_liste_krs_z_pliku(nazwa_pliku="krs_do_monitorowania.txt"):
    """Ta funkcja otwiera plik z listą numerów KRS i wczytuje je do pamięci."""
    try:
        # Otwieramy plik w trybie do odczytu ('r') z kodowaniem 'utf-8' (obsługa polskich znaków).
        with open(nazwa_pliku, 'r', encoding='utf-8') as plik:
            # Tworzymy listę, wczytując każdą linię z pliku,
            # o ile linia ta nie jest pusta. .strip() usuwa białe znaki (spacje, entery).
            lista_krs = [linia.strip() for linia in plik if linia.strip()]
        print(f"📄 Wczytano {len(lista_krs)} numerów KRS z pliku '{nazwa_pliku}'.")
        return lista_krs
    except FileNotFoundError:
        # Obsługa błędu, gdyby plik z listą KRS nie istniał.
        print(f"❌ BŁĄD: Nie znaleziono pliku '{nazwa_pliku}'! Upewnij się, że plik istnieje w repozytorium.")
        return []

def wczytaj_odbiorcow_z_pliku(nazwa_pliku="odbiorcy.txt"):
    """Ta funkcja otwiera plik z listą adresów e-mail i wczytuje je do pamięci."""
    try:
        with open(nazwa_pliku, 'r', encoding='utf-8') as plik:
            # Wczytujemy tylko te linie, które nie są puste i zawierają znak '@'.
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
        # Wysyłamy zapytanie GET i czekamy na odpowiedź.
        odpowiedz = requests.get(url)
        # Sprawdzamy, czy serwer odpowiedział poprawnie (kod 200 oznacza "OK").
        if odpowiedz.status_code == 200:
            # Zwracamy odpowiedź serwera w formacie JSON (który Python rozumie jako słownik).
            return odpowiedz.json()
    except requests.exceptions.RequestException:
        # Obsługa błędów połączenia z internetem.
        pass
    return None

def przeanalizuj_odpis_pod_katem_zmiany_kapitalu(odpis, data_poczatkowa, data_koncowa):
    """
    To serce programu. Analizuje odpis zgodnie z Twoją logiką:
    znajduje ostatni wpis i sprawdza, czy dotyczył on zmiany kapitału.
    """
    try:
        # Bezpieczne pobieranie danych z pliku JSON. Metoda .get() jest bezpieczna,
        # bo jeśli jakiegoś klucza nie ma, zwraca pustą listę/słownik, zamiast powodować błąd.
        historia_wpisow = odpis.get('odpis', {}).get('naglowekP', {}).get('wpis', [])
        dane_dzial1 = odpis.get('dane', {}).get('dzial1', {})
        historia_kapitalu = dane_dzial1.get('kapital', {}).get('wysokoscKapitaluZakladowego', [])
        
        if not historia_wpisow:
            return None

        # KROK 1 z Twojej logiki: Znajdź ostatni wpis w historii spółki (o najwyższym numerze)
        # Funkcja max() z dodatkowym argumentem 'key' pozwala znaleźć słownik z największą wartością pod kluczem 'numerWpisu'.
        ostatni_wpis = max(historia_wpisow, key=lambda wpis: int(wpis.get('numerWpisu', 0)))
        numer_ostatniego_wpisu = int(ostatni_wpis.get('numerWpisu', 0))

        if numer_ostatniego_wpisu == 0:
            return None

        # KROK 2 z Twojej logiki: Sprawdź, czy data ostatniego wpisu mieści się w zadanym przedziale
        data_zmiany = datetime.strptime(ostatni_wpis['dataWpisu'], "%d.%m.%Y").date()

        if not (data_poczatkowa <= data_zmiany <= data_koncowa):
            # Ostatnia zmiana jest poza naszym oknem czasowym, więc ją ignorujemy.
            return None
        
        # KROK 3 z Twojej logiki: Sprawdź, czy ostatni wpis faktycznie zmienił kapitał zakładowy
        # Przeszukujemy historię kapitału w poszukiwaniu rekordu, który został wprowadzony
        # przez ten ostatni wpis z KRS. Używamy 'next', aby znaleźć pierwszy pasujący element.
        wpis_zmieniajacy_kapital = next((
            kapital for kapital in historia_kapitalu 
            if int(kapital.get('nrWpisuWprow', -1)) == numer_ostatniego_wpisu
        ), None)

        # KROK 4 z Twojej logiki: Jeśli tak, zbierz dane do raportu. Jeśli nie, zignoruj.
        if wpis_zmieniajacy_kapital:
            # Sukces! Ostatnia zmiana dotyczyła kapitału. Zbieramy pozostałe dane.
            historia_nazw = dane_dzial1.get('danePodmiotu', {}).get('nazwa', [])
            # Szukamy aktualnej nazwy (tej, która nie ma numeru wykreślenia).
            aktualna_nazwa_info = next((nazwa for nazwa in historia_nazw if 'nrWpisuWykr' not in nazwa), None)
            nazwa_firmy = aktualna_nazwa_info['nazwa'] if aktualna_nazwa_info else "Nie udało się ustalić nazwy"

            # Szukamy poprzedniej wartości kapitału (tej, którą ostatni wpis wykreślił).
            poprzedni_kapital_info = next((k for k in historia_kapitalu if int(k.get('nrWpisuWykr', -1)) == numer_ostatniego_wpisu), None)
            poprzedni_kapital = poprzedni_kapital_info['wartosc'] if poprzedni_kapital_info else "Brak danych"

            # Zwracamy gotowy słownik z informacjami do raportu.
            return {
                "nazwa": nazwa_firmy,
                "krs": odpis.get('odpis', {}).get('naglowekP', {}).get('numerKRS'),
                "data_zmiany": ostatni_wpis['dataWpisu'],
                "nowy_kapital": wpis_zmieniajacy_kapital.get('wartosc', 'Brak danych'),
                "poprzedni_kapital": poprzedni_kapital
            }

    except (KeyError, IndexError, TypeError, ValueError) as e:
        # Jeśli wystąpi jakikolwiek niespodziewany błąd, skrypt wyświetli komunikat,
        # zamiast się "wykrzaczyć".
        krs_dla_bledu = odpis.get('odpis', {}).get('naglowekP', {}).get('numerKRS', ' nieznany')
        print(f"   -> ⚠️ Wystąpił błąd podczas analizy KRS {krs_dla_bledu}: {e}")
        return None
    
    # Jeśli ostatni wpis nie dotyczył kapitału, funkcja kończy działanie tutaj i nic nie zwraca.
    return None

def wyslij_email(tresc_raportu, odbiorcy):
    """Ta funkcja jest odpowiedzialna za wysłanie gotowego raportu e-mailem."""
    if not odbiorcy:
        print("Brak zdefiniowanych odbiorców. Pomijam wysyłanie e-maila.")
        return

    if not all([EMAIL_NADAWCY, HASLO_NADAWCY, SERWER_SMTP, PORT_SMTP]):
        print("❌ BŁĄD: Brak konfiguracji e-mail nadawcy. Sprawdź swoje sekrety na GitHubie.")
        print("Wiadomość nie została wysłana. Treść raportu poniżej:")
        print(tresc_raportu)
        return

    print(f"\n📧 Przygotowuję e-mail do wysłania do: {', '.join(odbiorcy)}...")

    # Tworzymy obiekt wiadomości
    wiadomosc = MIMEMultipart("alternative")
    wiadomosc["Subject"] = "Miesięczny raport zmian w kapitale zakładowym KRS"
    wiadomosc["From"] = EMAIL_NADAWCY
    wiadomosc["To"] = ", ".join(odbiorcy) # Nagłówek 'To' zawiera listę adresów

    # Dołączamy treść raportu do wiadomości
    wiadomosc.attach(MIMEText(tresc_raportu, "plain", "utf-8"))
    
    # Tworzymy bezpieczne połączenie z serwerem pocztowym
    kontekst_ssl = ssl.create_default_context()
    try:
        # Logujemy się do serwera i wysyłamy e-mail
        with smtplib.SMTP_SSL(SERWER_SMTP, PORT_SMTP, context=kontekst_ssl) as serwer:
            serwer.login(EMAIL_NADAWCY, HASLO_NADAWCY)
            serwer.sendmail(EMAIL_NADAWCY, odbiorcy, wiadomosc.as_string())
        print("✅ E-mail został wysłany pomyślnie!")
    except Exception as e:
        print(f"❌ Wystąpił błąd podczas wysyłania e-maila: {e}")

# ---------------------------------------------------------------------------
# KROK 4: Główna funkcja wykonująca skrypt (uruchomienie)
# ---------------------------------------------------------------------------
def main():
    """Główna funkcja, która steruje całym procesem, wywołując inne funkcje w odpowiedniej kolejności."""
    print("🚀 Start skryptu monitorującego zmiany w KRS.")
    
    lista_odbiorcow = wczytaj_odbiorcow_z_pliku()
    if not lista_odbiorcow:
        print("Brak zdefiniowanych odbiorców w pliku odbiorcy.txt. Kończę pracę.")
        return

    # Obliczamy zakres dat do sprawdzenia
    data_koncowa = datetime.now(timezone.utc).date()
    data_poczatkowa = data_koncowa - timedelta(days=DNI_DO_SPRAWDZENIA - 1)
    
    lista_krs_do_sprawdzenia = wczytaj_liste_krs_z_pliku()
    if not lista_krs_do_sprawdzenia:
        print("🏁 Lista KRS do sprawdzenia jest pusta. Koniec pracy.")
        return
        
    spolki_ze_zmiana_kapitalu = []
    liczba_spolek_do_sprawdzenia = len(lista_krs_do_sprawdzenia)
    
    # Pętla 'for' przechodzi przez każdy numer KRS z naszej listy
    for i, krs in enumerate(lista_krs_do_sprawdzenia, 1):
        print(f"🔎 Sprawdzam podmiot {i}/{liczba_spolek_do_sprawdzenia} (KRS: {krs})...")
        odpis = pobierz_pelny_odpis(krs)
        if odpis:
            # Wywołujemy funkcję analityczną
            informacje_o_zmianie = przeanalizuj_odpis_pod_katem_zmiany_kapitalu(odpis, data_poczatkowa, data_koncowa)
            if informacje_o_zmianie:
                # Jeśli funkcja coś zwróciła, to znaczy, że znalazła zmianę
                print(f"   -> ⭐ ZNALEZIONO ZMIANĘ KAPITAŁU dla {informacje_o_zmianie['nazwa']}!")
                spolki_ze_zmiana_kapitalu.append(informacje_o_zmianie)
        # Robimy krótką przerwę
        time.sleep(OPÓŹNIENIE_API)

    # Po sprawdzeniu wszystkich spółek, tworzymy i wysyłamy raport
    if spolki_ze_zmiana_kapitalu:
        print(f"\n📊 Znaleziono {len(spolki_ze_zmiana_kapitalu)} spółek ze zmianą kapitału.")
        # Przygotowujemy treść e-maila
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
        # Można odkomentować poniższe linie, aby otrzymywać e-mail nawet, gdy nie ma zmian
        # tresc_raportu = f"W okresie od {data_poczatkowa.strftime('%d.%m.%Y')} do {data_koncowa.strftime('%d.%m.%Y')} nie odnotowano żadnych zmian w kapitale zakładowym na Twojej liście monitorowanych spółek."
        # wyslij_email(tresc_raportu, lista_odbiorcow)

    print("🏁 Skrypt zakończył pracę.")

# Ten fragment kodu powoduje uruchomienie funkcji main() tylko wtedy,
# gdy plik jest wykonywany bezpośrednio.
if __name__ == "__main__":
    main()
