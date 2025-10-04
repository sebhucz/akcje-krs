# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# KROK 1: Importowanie potrzebnych bibliotek
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
# KROK 2: Konfiguracja skryptu
# ---------------------------------------------------------------------------
# Odczytywanie konfiguracji e-mail z GitHub Secrets
EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 465))

# Ustawienia API
API_BASE_URL = "https://api-krs.ms.gov.pl/api/krs"
API_CALL_DELAY = 1

# Ustawienia zakresu dat
DAYS_TO_CHECK = 30

# ---------------------------------------------------------------------------
# KROK 3: Definicje funkcji (logika skryptu)
# ---------------------------------------------------------------------------

def read_krs_list_from_file(filename="krs_do_monitorowania.txt"):
    """Wczytuje listę numerów KRS z pliku tekstowego."""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            krs_list = [line.strip() for line in f if line.strip()]
        print(f"📄 Wczytano {len(krs_list)} numerów KRS z pliku '{filename}'.")
        return krs_list
    except FileNotFoundError:
        print(f"❌ BŁĄD: Nie znaleziono pliku '{filename}'! Upewnij się, że plik istnieje w repozytorium.")
        return []

def read_recipients_from_file(filename="odbiorcy.txt"):
    """Wczytuje listę adresów e-mail odbiorców z pliku tekstowego."""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            recipients = [line.strip() for line in f if line.strip() and '@' in line]
        print(f"📧 Wczytano {len(recipients)} odbiorców z pliku '{filename}'.\n")
        return recipients
    except FileNotFoundError:
        print(f"❌ BŁĄD: Nie znaleziono pliku odbiorców '{filename}'! Upewnij się, że plik istnieje.")
        return []

def get_full_record(krs_number):
    """Pobiera pełny odpis z KRS dla danego numeru."""
    url = f"{API_BASE_URL}/OdpisPelny/{krs_number}?rejestr=P"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            return response.json()
    except requests.exceptions.RequestException:
        pass
    return None

# NOWA WERSJA FUNKCJI ANALIZUJĄCEJ - ZGODNA Z TWOJĄ LOGIKĄ
def analyze_record_for_capital_change(record, start_date, end_date):
    """
    Analizuje odpis zgodnie z nową logiką: znajduje ostatni wpis i sprawdza,
    czy dotyczył on zmiany kapitału.
    """
    try:
        # Bezpieczne pobranie potrzebnych danych
        entry_history = record.get('odpis', {}).get('naglowekP', {}).get('wpis', [])
        dane_dzial1 = record.get('dane', {}).get('dzial1', {})
        capital_history = dane_dzial1.get('kapital', {}).get('wysokoscKapitaluZakladowego', [])
        
        if not entry_history:
            return None

        # KROK 1: Znajdź ostatni wpis w historii spółki (o najwyższym numerze)
        last_entry = max(entry_history, key=lambda e: int(e.get('numerWpisu', 0)))
        last_entry_number = int(last_entry.get('numerWpisu', 0))

        if last_entry_number == 0:
            return None

        # KROK 2: Sprawdź, czy data tego ostatniego wpisu mieści się w zadanym przedziale
        date_of_change = datetime.strptime(last_entry['dataWpisu'], "%d.%m.%Y").date()

        if not (start_date <= date_of_change <= end_date):
            # Ostatnia zmiana jest poza naszym oknem czasowym, więc ją ignorujemy.
            return None
        
        # KROK 3: Sprawdź, czy ostatni wpis faktycznie zmienił kapitał zakładowy
        # Szukamy w historii kapitału wpisu, który został wprowadzony przez ten ostatni wpis z KRS.
        capital_change_entry = next((
            c for c in capital_history 
            if int(c.get('nrWpisuWprow', -1)) == last_entry_number
        ), None)

        # KROK 4: Jeśli tak, zbierz dane do raportu. Jeśli nie, zignoruj.
        if capital_change_entry:
            # Sukces! Ostatnia zmiana dotyczyła kapitału. Zbieramy dane.
            name_history = dane_dzial1.get('danePodmiotu', {}).get('nazwa', [])
            current_name_info = next((n for n in name_history if 'nrWpisuWykr' not in n), None)
            company_name = current_name_info['nazwa'] if current_name_info else "Nie udało się ustalić nazwy"

            previous_capital_info = next((c for c in capital_history if int(c.get('nrWpisuWykr', -1)) == last_entry_number), None)
            previous_capital = previous_capital_info['wartosc'] if previous_capital_info else "Brak danych"

            return {
                "nazwa": company_name,
                "krs": record.get('odpis', {}).get('naglowekP', {}).get('numerKRS'),
                "data_zmiany": last_entry['dataWpisu'],
                "nowy_kapital": capital_change_entry.get('wartosc', 'Brak danych'),
                "poprzedni_kapital": previous_capital
            }

    except (KeyError, IndexError, TypeError, ValueError) as e:
        krs_for_error = record.get('odpis', {}).get('naglowekP', {}).get('numerKRS', ' nieznany')
        print(f"   -> ⚠️ Wystąpił błąd podczas analizy KRS {krs_for_error}: {e}")
        return None
    
    # Jeśli ostatni wpis nie dotyczył kapitału, funkcja kończy działanie tutaj
    return None

def send_email(report_body, recipients):
    """Wysyła raport e-mailem do podanej listy odbiorców."""
    if not recipients:
        print("Brak zdefiniowanych odbiorców. Pomijam wysyłanie e-maila.")
        return

    if not all([EMAIL_SENDER, EMAIL_PASSWORD, SMTP_SERVER, SMTP_PORT]):
        print("❌ BŁĄD: Brak konfiguracji e-mail nadawcy. Sprawdź GitHub Secrets.")
        print("Wiadomość nie została wysłana. Treść raportu poniżej:")
        print(report_body)
        return

    print(f"\n📧 Przygotowuję e-mail do wysłania do: {', '.join(recipients)}...")

    message = MIMEMultipart("alternative")
    message["Subject"] = "Miesięczny raport zmian w kapitale zakładowym KRS"
    message["From"] = EMAIL_SENDER
    message["To"] = ", ".join(recipients)

    message.attach(MIMEText(report_body, "plain", "utf-8"))
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, recipients, message.as_string())
        print("✅ E-mail został wysłany pomyślnie!")
    except Exception as e:
        print(f"❌ Wystąpił błąd podczas wysyłania e-maila: {e}")

# ---------------------------------------------------------------------------
# KROK 4: Główna funkcja wykonująca skrypt
# ---------------------------------------------------------------------------
def main():
    """Główna funkcja, która steruje całym procesem."""
    print("🚀 Start skryptu monitorującego zmiany w KRS.")
    
    recipients_list = read_recipients_from_file()
    if not recipients_list:
        print("Brak zdefiniowanych odbiorców w pliku odbiorcy.txt. Kończę pracę.")
        return

    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=DAYS_TO_CHECK - 1)
    
    krs_to_check = read_krs_list_from_file()
    if not krs_to_check:
        print("🏁 Lista KRS do sprawdzenia jest pusta. Koniec pracy.")
        return
        
    companies_with_capital_change = []
    total_to_check = len(krs_to_check)
    
    for i, krs in enumerate(krs_to_check, 1):
        print(f"🔎 Sprawdzam podmiot {i}/{total_to_check} (KRS: {krs})...")
        record = get_full_record(krs)
        if record:
            change_info = analyze_record_for_capital_change(record, start_date, end_date)
            if change_info:
                print(f"   -> ⭐ ZNALEZIONO ZMIANĘ KAPITAŁU dla {change_info['nazwa']}!")
                companies_with_capital_change.append(change_info)
        time.sleep(API_CALL_DELAY)

    if companies_with_capital_change:
        print(f"\n📊 Znaleziono {len(companies_with_capital_change)} spółek ze zmianą kapitału.")
        report_lines = [
            f"Raport zmian w kapitale zakładowym monitorowanych spółek w okresie od {start_date.strftime('%d.%m.%Y')} do {end_date.strftime('%d.%m.%Y')}.\n",
            f"Znaleziono {len(companies_with_capital_change)} podmiotów:\n",
            "--------------------------------------------------"
        ]
        for company in companies_with_capital_change:
            line = (
                f"Nazwa: {company['nazwa']}\n"
                f"KRS: {company['krs']}\n"
                f"Data zmiany: {company['data_zmiany']}\n"
                f"Poprzedni kapitał: {company['poprzedni_kapital']} PLN\n"
                f"Nowy kapitał: {company['nowy_kapital']} PLN\n"
                "--------------------------------------------------"
            )
            report_lines.append(line)
        report_body = "\n".join(report_lines)
        send_email(report_body, recipients_list)
    else:
        print("\n✅ Na Twojej liście nie znaleziono żadnych spółek ze zmianą kapitału zakładowego w badanym okresie.")
        # report_text = f"W okresie od {start_date.strftime('%d.%m.%Y')} do {end_date.strftime('%d.%m.%Y')} nie odnotowano żadnych zmian w kapitale zakładowym na Twojej liście monitorowanych spółek."
        # send_email(report_text, recipients_list)

    print("🏁 Skrypt zakończył pracę.")

if __name__ == "__main__":
    main()
