-- Database Schema for Polish Medicinal Products Registry (Rejestr Produktów Leczniczych - RPL)

PRAGMA foreign_keys = ON;

-- 1. Main medicinal products table
CREATE TABLE IF NOT EXISTS produkty_lecznicze (
    id INTEGER PRIMARY KEY,
    nazwa_produktu TEXT NOT NULL,
    rodzaj_preparatu TEXT NOT NULL,
    nazwa_powszechnie_stosowana TEXT NOT NULL,
    nazwa_poprzednia_produktu TEXT,
    moc TEXT,
    nazwa_postaci_farmaceutycznej TEXT NOT NULL,
    podmiot_odpowiedzialny TEXT NOT NULL,
    typ_procedury TEXT NOT NULL,
    numer_pozwolenia TEXT,
    waznosc_pozwolenia TEXT,
    podstawa_prawna TEXT,
    zakaz_stosowania_u_zwierzat TEXT,
    ulotka TEXT,
    charakterystyka TEXT,
    etykieto_ulotka TEXT,
    etykieto_ulotka_import_rownolegly TEXT,
    oznaczenie_opakowan_import_rownolegly TEXT,
    ulotka_import_rownolegly TEXT
);

-- 2. ATC Codes (Anatomical Therapeutic Chemical Classification)
CREATE TABLE IF NOT EXISTS kody_atc (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produkt_id INTEGER NOT NULL REFERENCES produkty_lecznicze(id) ON DELETE CASCADE,
    kod_atc TEXT NOT NULL
);

-- 3. Routes of administration
CREATE TABLE IF NOT EXISTS drogi_podania (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produkt_id INTEGER NOT NULL REFERENCES produkty_lecznicze(id) ON DELETE CASCADE,
    droga_podania_nazwa TEXT NOT NULL
);

-- 4. Active substances
CREATE TABLE IF NOT EXISTS substancje_czynne (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produkt_id INTEGER NOT NULL REFERENCES produkty_lecznicze(id) ON DELETE CASCADE,
    nazwa_substancji TEXT NOT NULL,
    ilosc_substancji TEXT NOT NULL,
    jednostka_miary_ilosci_substancji TEXT NOT NULL,
    ilosc_preparatu TEXT,
    jednostka_miary_ilosci_preparatu TEXT,
    inny_opis_ilosci TEXT
);

-- 5. Packaging
CREATE TABLE IF NOT EXISTS opakowania (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opakowanie_id INTEGER NOT NULL,
    produkt_id INTEGER NOT NULL REFERENCES produkty_lecznicze(id) ON DELETE CASCADE,
    kod_gtin TEXT,
    kategoria_dostepnosci TEXT NOT NULL,
    skasowane TEXT NOT NULL,
    numer_eu TEXT,
    dystrybutor_rownolegly TEXT
);

-- 6. Packaging units (details of units inside each package)
CREATE TABLE IF NOT EXISTS jednostki_opakowania (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opakowanie_id INTEGER NOT NULL,
    produkt_id INTEGER NOT NULL REFERENCES produkty_lecznicze(id) ON DELETE CASCADE,
    liczba_opakowan TEXT,
    rodzaj_opakowania TEXT,
    pojemnosc TEXT,
    jednostka_pojemnosci TEXT,
    informacje_dodatkowe TEXT
);

-- 7. President approvals (Zgody Prezesa) & Foreign GTINs
CREATE TABLE IF NOT EXISTS zgody_prezesa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opakowanie_id INTEGER NOT NULL,
    produkt_id INTEGER NOT NULL REFERENCES produkty_lecznicze(id) ON DELETE CASCADE,
    nr_zgody_prezesa TEXT NOT NULL,
    gtin_zagraniczny TEXT
);

-- 8. Manufacturers / Importers
CREATE TABLE IF NOT EXISTS wytworcy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produkt_id INTEGER NOT NULL REFERENCES produkty_lecznicze(id) ON DELETE CASCADE,
    nazwa_wytworcy_importera TEXT,
    kraj_wytworcy_importera TEXT,
    kraj_eksportu TEXT,
    podmiot_odpowiedzialny_w_kraju_eksportu TEXT
);

-- 9. Educational materials
CREATE TABLE IF NOT EXISTS materialy_edukacyjne (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produkt_id INTEGER NOT NULL REFERENCES produkty_lecznicze(id) ON DELETE CASCADE,
    typ_odbiorcy TEXT NOT NULL, -- 'dla_pacjenta' | 'dla_medyka'
    material TEXT NOT NULL,
    nazwa_materialu TEXT NOT NULL
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_produkty_nazwa ON produkty_lecznicze(nazwa_produktu);
CREATE INDEX IF NOT EXISTS idx_produkty_nazwa_powszechna ON produkty_lecznicze(nazwa_powszechnie_stosowana);
CREATE INDEX IF NOT EXISTS idx_produkty_podmiot ON produkty_lecznicze(podmiot_odpowiedzialny);
CREATE INDEX IF NOT EXISTS idx_kody_atc_produkt_id ON kody_atc(produkt_id);
CREATE INDEX IF NOT EXISTS idx_kody_atc_kod ON kody_atc(kod_atc);
CREATE INDEX IF NOT EXISTS idx_drogi_podania_produkt_id ON drogi_podania(produkt_id);
CREATE INDEX IF NOT EXISTS idx_substancje_produkt_id ON substancje_czynne(produkt_id);
CREATE INDEX IF NOT EXISTS idx_substancje_nazwa ON substancje_czynne(nazwa_substancji);
CREATE INDEX IF NOT EXISTS idx_opakowania_produkt_id ON opakowania(produkt_id);
CREATE INDEX IF NOT EXISTS idx_opakowania_gtin ON opakowania(kod_gtin);
CREATE INDEX IF NOT EXISTS idx_wytworcy_produkt_id ON wytworcy(produkt_id);
