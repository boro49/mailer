import streamlit as st
import pandas as pd
import io
import csv
import os
import re
import zipfile
import tempfile
import requests
import base64
from bs4 import BeautifulSoup
from jinja2 import Template
import shutil

# ------------- KONFIGURACJA I INICJALIZACJA --------------------
OUTPUT_FOLDER = 'generated_mails'
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Folder tymczasowy na rozpakowany szablon
TEMPLATE_TEMP_FOLDER = tempfile.mkdtemp(prefix="template_")

st.set_page_config(page_title="Generator Paczek Mailingowych - Scrap + Template", layout="wide")

# Globalna zmienna przechowująca kod szablonu
global_template_code = None

# ------------- FUNKCJE POMOCNICZE --------------------

def extract_template_zip(uploaded_zip):
    """Rozpakowuje przesłany plik ZIP do folderu tymczasowego i zwraca ścieżkę do pliku index.html."""
    with tempfile.TemporaryDirectory() as tmpdirname:
        with zipfile.ZipFile(uploaded_zip, "r") as zip_ref:
            zip_ref.extractall(TEMPLATE_TEMP_FOLDER)
    index_path = os.path.join(TEMPLATE_TEMP_FOLDER, "index.html")
    if not os.path.exists(index_path):
        st.error("W archiwum ZIP nie znaleziono pliku index.html!")
        return None
    return index_path

def load_template_from_file(template_path):
    """Wczytuje zawartość pliku szablonu."""
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        st.error(f"Błąd podczas wczytywania szablonu: {e}")
        return None

def get_used_template_variables(template_code):
    """Zwraca zestaw nazw zmiennych używanych w kodzie Jinja2."""
    return set(re.findall(r'{{\s*(\w+)\s*}}', template_code))

def download_image(image_url, dest_folder):
    """Pobiera obraz z URL i zapisuje go w folderze dest_folder.
    Jeśli image_url zaczyna się od 'data:', zwraca go bez zmian.
    """
    if not image_url:
        return None
    # Jeżeli już jest Data URI, nie próbuj pobierać ponownie
    if image_url.startswith("data:"):
        return image_url
    try:
        response = requests.get(image_url, timeout=10)
        response.raise_for_status()
        filename = os.path.basename(image_url.split('?')[0])
        local_path = os.path.join(dest_folder, filename)
        with open(local_path, 'wb') as f:
            f.write(response.content)
        return local_path
    except Exception as e:
        st.error(f"Błąd pobierania obrazu z {image_url}: {e}")
        return None


def embed_image_as_data_uri(image_path):
    """Odczytuje obraz z podanej ścieżki i zwraca data URI (base64)."""
    if not os.path.exists(image_path):
        return ""
    ext = os.path.splitext(image_path)[1][1:].lower()  # rozszerzenie bez kropki
    if ext == "svg":
        mime = "image/svg+xml"
    else:
        mime = f"image/{ext}"
    try:
        with open(image_path, "rb") as f:
            data = f.read()
        b64 = base64.b64encode(data).decode()
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        st.error(f"Błąd przy konwersji obrazu do data URI: {e}")
        return ""

def zip_output_for_folder(folder, package_identifier):
    """Zipuje folder, nadając archiwum nazwę <package_identifier>.zip"""
    zip_filename = f"{package_identifier}"
    shutil.make_archive(zip_filename, 'zip', folder)
    return f"{zip_filename}.zip"

def scrap_page(url):
    """
    Scrapuje stronę podaną przez URL i zwraca słownik z kluczami: title, img, lead.
    title: tekst pierwszego tagu H1.
    img: adres URL obrazu z selektora 'div.entry-image > img'.
    lead: tekst z 'div.entry-lead'. Jeśli nie znaleziono tego elementu,
          próbuje odszukać 'div.article__content' i zwraca pierwsze 150 znaków tekstu.
    """
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, 'html.parser')
        
        # Tytuł: pierwszy tag H1
        h1 = soup.find('h1')
        title = h1.get_text(strip=True) if h1 else ""
        
        # Obraz: selektor "div.entry-image > img"
        img_tag = soup.select_one("div.entry-image > img")
        img = img_tag.get("src", "") if img_tag else ""
        
        # Lead: próba pobrania z div.entry-lead
        lead_tag = soup.select_one("div.entry-lead")
        if lead_tag:
            lead_text = lead_tag.get_text(strip=True)
        else:
            # Jeśli nie znaleziono, próbujemy z div.article__content – pobieramy czysty tekst
            article_tag = soup.select_one("div.article__content")
            lead_text = article_tag.get_text(separator=" ", strip=True) if article_tag else ""
        
        # Ograniczenie tekstu do pierwszych 150 znaków
        lead = lead_text[:150]
        
        return {"title": title, "img": img, "lead": lead}
    except Exception as e:
        st.error(f"Błąd scrapowania {url}: {e}")
        return {"title": "", "img": "", "lead": ""}


def save_data_uri_as_file(data_uri, dest_folder, default_filename="image"):
    """
    Jeśli data_uri zaczyna się od 'data:', dekoduje zawartość base64 i zapisuje ją jako plik.
    Na podstawie typu MIME wyciągamy rozszerzenie.
    Zwraca pełną ścieżkę do zapisanego pliku.
    """
    try:
        # Oczekujemy formatu "data:image/webp;base64,PD94bWwg..."
        if not data_uri.startswith("data:"):
            return None
        header, b64data = data_uri.split(",", 1)
        # header przykładowo: "data:image/webp;base64"
        mime_part = header.split(";")[0]  # "data:image/webp"
        mime_type = mime_part.split(":")[1]  # "image/webp"
        ext = mime_type.split("/")[1]       # "webp"
        filename = f"{default_filename}.{ext}"
        dest_path = os.path.join(dest_folder, filename)
        with open(dest_path, "wb") as f:
            f.write(base64.b64decode(b64data))
        return dest_path
    except Exception as e:
        st.error(f"Błąd zapisu data URI do pliku: {e}")
        return None


def process_scrape_csv(file_bytes):
    """
    Scrapuje dane dla każdego wiersza CSV.
    CSV powinien zawierać kolumny: ID, url1, url2.
    Dla url1 i url2 scrapuje tytuł (H1), adres URL obrazu (div.entry-image > img) oraz lead (div.entry-lead)
    i dodaje do danych nowe kolumny:
      title1, img1, lead1, title2, img2, lead2.
    Zwraca listę słowników (każdy odpowiada wierszowi).
    """
    file_io = io.StringIO(file_bytes.decode('utf-8-sig'))
    reader = list(csv.DictReader(file_io, delimiter=';'))
    if not reader:
        st.error("Brak danych w CSV.")
        return []
    for row in reader:
        # Scrapowanie dla url1
        if "url1" in row and row["url1"]:
            data1 = scrap_page(row["url1"])
            row["url1"] = row["url1"]
            row["title1"] = data1["title"]
            row["img1"] = data1["img"]
            row["lead1"] = data1["lead"]
        else:
            row["url1"] = row["url1"]
            row["title1"] = ""
            row["img1"] = ""
            row["lead1"] = ""
        # Scrapowanie dla url2
        if "url2" in row and row["url2"]:
            data2 = scrap_page(row["url2"])
            row["url2"] = row["url2"]
            row["title2"] = data2["title"]
            row["img2"] = data2["img"]
            row["lead2"] = data2["lead"]
        else:
            row["url2"] = row["url2"]
            row["title2"] = ""
            row["img2"] = ""
            row["lead2"] = ""
    return reader

def process_csv(data_rows, template_code, naming_variable, dynamic_image_columns=None):
    """
    Generuje paczki na podstawie przetworzonych danych (lista słowników).
    Jeśli naming_variable jest podana, używa jej wartości do nazwy paczki, w przeciwnym razie numeruje paczki.
    Jeśli dynamic_image_columns jest podana, dla każdej z nich pobiera obraz i zastępuje wartość nazwą pliku.
    Zwraca listę ścieżek do wygenerowanych ZIP-ów.
    """
    zip_files = []
    template_obj = Template(template_code)
    for row_index, row in enumerate(data_rows, start=1):
        if naming_variable and naming_variable in row and row[naming_variable]:
            package_identifier = row[naming_variable]
        else:
            package_identifier = str(row_index)
            
        package_folder = os.path.join(OUTPUT_FOLDER, f"{package_identifier}")
        os.makedirs(package_folder, exist_ok=True)
        
        # Przetwarzanie każdej wybranej kolumny z dynamicznym obrazem
        if dynamic_image_columns:
            for col in dynamic_image_columns:
                if col in row and row[col]:
                    # Jeśli wartość zaczyna się od "http", pobieramy obraz z internetu.
                    if row[col].startswith("http"):
                        image_path = download_image(row[col], package_folder)
                        if image_path:
                            row[col] = os.path.basename(image_path)
                        else:
                            row[col] = row[col]
                    # Jeśli wartość zaczyna się od "data:", zapisujemy ją do pliku.
                    elif row[col].startswith("data:"):
                        image_path = save_data_uri_as_file(row[col], package_folder, default_filename=col)
                        if image_path:
                            row[col] = os.path.basename(image_path)
                        else:
                            row[col] = row[col]
                    else:
                        row[col] = row[col]


        try:
            rendered_html = template_obj.render(**row)
        except Exception as e:
            st.error(f"Błąd renderowania (pakiet {package_identifier}): {e}")
            continue

        try:
            shutil.copytree(TEMPLATE_TEMP_FOLDER, package_folder, dirs_exist_ok=True)
        except Exception as e:
            st.error(f"Błąd kopiowania zasobów szablonu dla paczki {package_identifier}: {e}")
            continue

        output_html_path = os.path.join(package_folder, "index.html")
        try:
            with open(output_html_path, 'w', encoding='utf-8') as f:
                f.write(rendered_html)
        except Exception as e:
            st.error(f"Błąd zapisu HTML dla paczki {package_identifier}: {e}")
            continue

        st.info(f"Wygenerowano paczkę: {package_identifier}")
        zip_file = zip_output_for_folder(package_folder, package_identifier)
        zip_files.append(zip_file)
    return zip_files

def inline_base_images(html_text, base_folder):
    """
    Szuka w html_text wszystkich atrybutów src, które nie zaczynają się od "data:".
    Dla znalezionych ścieżek traktuje je jako relatywne względem base_folder i
    jeśli odpowiadają rzeczywistym plikom, konwertuje je na Data URI.
    """
    def replace_src(match):
        src = match.group(1)
        # Jeśli src już jest Data URI, nic nie zmieniamy
        if src.startswith("data:"):
            return match.group(0)
        # Tworzymy pełną ścieżkę do pliku
        file_path = os.path.join(base_folder, src)
        if os.path.exists(file_path):
            data_uri = embed_image_as_data_uri(file_path)
            return match.group(0).replace(src, data_uri)
        else:
            return match.group(0)
    pattern = re.compile(r'src=["\'](.*?)["\']')
    return pattern.sub(replace_src, html_text)

def generate_preview(file_bytes, template_code, dynamic_image_columns=None):
    """
    Generuje podgląd dla pierwszego wiersza. Jeśli w st.session_state.scraped_data 
    znajdują się dane (lista słowników), to użyjemy pierwszego wiersza z tych danych.
    Następnie dla kolumn dynamicznych, jeśli podanych, pobieramy obrazy i zamieniamy na Data URI.
    Na końcu renderujemy szablon i inlinujemy obrazy z zasobów bazowego szablonu.
    """
    # Sprawdzenie, czy posiadamy już zescrapowane dane
    if "scraped_data" in st.session_state and st.session_state.scraped_data:
        preview_row = st.session_state.scraped_data[0]
        st.info("Używam zescrapowanych danych do podglądu.")
    else:
        file_io = io.StringIO(file_bytes.decode('utf-8-sig'))
        reader = list(csv.DictReader(file_io, delimiter=';'))
        if not reader:
            st.error("Brak danych w CSV.")
            return None
        preview_row = reader[0]
    
    preview_folder = os.path.join(OUTPUT_FOLDER, "preview")
    os.makedirs(preview_folder, exist_ok=True)
    
    # Przetwarzanie dynamicznych kolumn obrazów
    if dynamic_image_columns:
        for col in dynamic_image_columns:
            if col in preview_row and preview_row[col]:
                # Jeśli wartość zaczyna się od "http", pobieramy obraz i konwertujemy na Data URI.
                if preview_row[col].startswith("http"):
                    image_path = download_image(preview_row[col], preview_folder)
                    embedded_image = embed_image_as_data_uri(image_path)
                    preview_row[col] = embedded_image if embedded_image else preview_row[col]
                # Jeśli już jest Data URI – pozostawiamy taką wartość.
                elif preview_row[col].startswith("data:"):
                    preview_row[col] = preview_row[col]
    
    try:
        template_obj = Template(template_code)
        preview_html = template_obj.render(**preview_row)
    except Exception as e:
        st.error(f"Błąd podczas generowania podglądu: {e}")
        return None

    # Opcjonalnie, inline'owanie obrazów z zasobów bazowego szablonu:
    inlined_html = inline_base_images(preview_html, preview_folder)
    return inlined_html


def copy_button_html(text_to_copy, button_text="Kopiuj"):
    """Zwraca HTML z przyciskiem kopiującym podany tekst do schowka."""
    html_code = f"""
    <input type="text" value="{text_to_copy}" id="copyInput" readonly style="width:200px;">
    <button onclick="navigator.clipboard.writeText(document.getElementById('copyInput').value)">
        {button_text}
    </button>
    """
    return html_code

# ------------- INTERFEJS UŻYTKOWNIKA --------------------

st.title("Generator Paczek Mailingowych z Scrapowaniem Danych")

st.header("1. Wgraj dane (CSV)")
uploaded_csv = st.file_uploader("Wgraj plik CSV (kolumny: ID; url1; url2)", type=["csv"], key="csv_uploader")
csv_columns = None
if uploaded_csv:
    try:
        df = pd.read_csv(uploaded_csv, delimiter=';', encoding='utf-8-sig')
        st.write("Podgląd danych CSV:", df.head())
        csv_columns = list(df.columns)
        st.write("Dostępne zmienne:", csv_columns)
    except Exception as e:
        st.error(f"Błąd podczas wczytywania CSV: {e}")

st.markdown("---")
st.header("2. Przetwórz dane (Scrap)")
if uploaded_csv:
    if st.button("Przetwórz dane"):
        data_rows = process_scrape_csv(uploaded_csv.getvalue())
        if data_rows:
            st.session_state.scraped_data = data_rows  # zapisz scrapowane dane
            df_scraped = pd.DataFrame(data_rows)
            st.write("Podgląd danych po scrapowaniu:", df_scraped)
        else:
            st.error("Błąd podczas scrapowania danych.")

st.markdown("---")
st.header("3. Wgraj szablon maila (ZIP)")
uploaded_zip = st.file_uploader("Wgraj plik ZIP zawierający szablon (index.html + grafiki)", type=["zip"], key="zip_uploader")
if uploaded_zip:
    template_path = extract_template_zip(uploaded_zip)
    if template_path:
        global_template_code = load_template_from_file(template_path)
        if global_template_code:
            st.success("Szablon został wczytany.")
            st.subheader("Podgląd szablonu HTML")
            st.code(global_template_code, language="html")

st.markdown("---")
st.header("4. Wybór zmiennej na nazwę paczki")
naming_variable = st.selectbox(
    "Wybierz zmienną, której wartość posłuży jako nazwa paczki (lub wybierz 'Domyślne numerowanie')",
    options=["Domyślne numerowanie"] + (csv_columns if csv_columns else [])
)

st.markdown("---")
st.header("5. Podgląd / Generowanie paczek")
col1, col2 = st.columns(2)

with col1:
    if st.button("Generuj Podgląd paczki"):
        if not uploaded_csv or not global_template_code:
            st.error("Wgraj plik CSV oraz szablon ZIP!")
        else:
            used_vars = get_used_template_variables(global_template_code)
            dynamic_images = [col for col in ["img1", "img2"] if col in used_vars]
            preview_html = generate_preview(uploaded_csv.getvalue(), global_template_code, dynamic_image_columns=dynamic_images)
            if preview_html:
                st.markdown("### Podgląd wygenerowanego HTML:")
                st.components.v1.html(preview_html, height=600, scrolling=True)


with col2:
    if st.button("Generuj wszystkie paczki"):
        if not uploaded_csv or not global_template_code:
            st.error("Wgraj plik CSV oraz szablon ZIP!")
        else:
            if "scraped_data" in st.session_state and st.session_state.scraped_data:
                data_rows = st.session_state.scraped_data
            else:
                st.error("Nie przetworzono danych (scrap).")
                data_rows = []
            if data_rows:
                naming_var = None if naming_variable == "Domyślne numerowanie" else naming_variable
                used_vars = get_used_template_variables(global_template_code)
                dynamic_images = [col for col in ["img1", "img2"] if col in used_vars]
                zip_files = process_csv(data_rows, global_template_code, naming_var, dynamic_image_columns=dynamic_images)
                # Później przyciski do pobrania paczek...
                if zip_files:
                    st.success("Generowanie paczek zakończone!")
                    st.write("Pobierz poszczególne paczki:")
                    for zip_file in zip_files:
                        with open(zip_file, "rb") as f:
                            st.download_button(label=f"Pobierz {zip_file}", data=f, file_name=zip_file)
                    all_zip = "wszystkie_paczki.zip"
                    shutil.make_archive("wszystkie_paczki", 'zip', OUTPUT_FOLDER)
                    with open(all_zip, "rb") as f:
                        st.download_button(label="Pobierz wszystkie paczki", data=f, file_name=all_zip)
                else:
                    st.error("Nie wygenerowano żadnych paczek. Sprawdź dane wejściowe lub popraw błędy.")
