use serde::Deserialize;

pub fn deserialize_ludzki<'de, D>(deserializer: D) -> Result<Vec<ProduktLeczniczy>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let products = Vec::<ProduktLeczniczy>::deserialize(deserializer)?;
    let filtered = products
        .into_iter()
        .filter(|p| p.rodzaj_preparatu == "ludzki")
        .collect();
    Ok(filtered)
}

#[derive(Debug, Deserialize)]
#[serde(rename = "produktyLecznicze")]
pub struct ProduktyLecznicze {
    #[serde(rename = "@stanNaDzien")]
    pub stan_na_dzien: String,

    #[serde(
        rename = "produktLeczniczy",
        deserialize_with = "deserialize_ludzki",
        default
    )]
    pub produkty: Vec<ProduktLeczniczy>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct ProduktLeczniczy {
    #[serde(rename = "@nazwaProduktu")]
    pub nazwa_produktu: String,

    #[serde(rename = "@rodzajPreparatu")]
    pub rodzaj_preparatu: String,

    #[serde(rename = "@nazwaPowszechnieStosowana")]
    pub nazwa_powszechnie_stosowana: String,

    #[serde(rename = "@nazwaPoprzedniaProduktu")]
    pub nazwa_poprzednia_produktu: String,

    #[serde(rename = "@moc")]
    pub moc: Option<String>,

    #[serde(rename = "@nazwaPostaciFarmaceutycznej")]
    pub nazwa_postaci_farmaceutycznej: String,

    #[serde(rename = "@podmiotOdpowiedzialny")]
    pub podmiot_odpowiedzialny: String,

    #[serde(rename = "@typProcedury")]
    pub typ_procedury: String,

    #[serde(rename = "@numerPozwolenia")]
    pub numer_pozwolenia: Option<String>,

    #[serde(rename = "@waznoscPozwolenia")]
    pub waznosc_pozwolenia: Option<String>,

    #[serde(rename = "@podstawaPrawna")]
    pub podstawa_prawna: Option<String>,

    #[serde(rename = "@zakazStosowaniaUZwierzat")]
    pub zakaz_stosowania_u_zwierzat: Option<String>,

    #[serde(rename = "@ulotka")]
    pub ulotka: Option<String>,

    #[serde(rename = "@charakterystyka")]
    pub charakterystyka: Option<String>,

    #[serde(rename = "@id")]
    pub id: u64,

    #[serde(rename = "@etykietoUlotka")]
    pub etykieto_ulotka: Option<String>,

    #[serde(rename = "@etykietoUlotkaImportRownolegly")]
    pub etykieto_ulotka_import_rownolegly: Option<String>,

    #[serde(rename = "@oznaczenieOpakowanImportRownolegly")]
    pub oznaczenie_opakowan_import_rownolegly: Option<String>,

    #[serde(rename = "@ulotkaImportRownolegly")]
    pub ulotka_import_rownolegly: Option<String>,

    // Child elements
    #[serde(rename = "kodyATC", default)]
    pub kody_atc: Option<KodyAtc>,

    #[serde(rename = "drogiPodania", default)]
    pub drogi_podania: Option<DrogiPodania>,

    #[serde(rename = "substancjeCzynne", default)]
    pub substancje_czynne: Option<SubstancjeCzynne>,

    #[serde(rename = "opakowania", default)]
    pub opakowania: Option<Opakowania>,

    #[serde(rename = "daneOWytworcy", default)]
    pub dane_o_wytworcy: Option<DaneOWytworcy>,

    #[serde(rename = "materialyEdukacyjne", default)]
    pub materialy_edukacyjne: Option<MaterialyEdukacyjne>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct KodyAtc {
    #[serde(rename = "kodATC", default)]
    pub kody: Vec<String>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct DrogiPodania {
    #[serde(rename = "drogaPodania", default)]
    pub drogi: Vec<DrogaPodania>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct DrogaPodania {
    #[serde(rename = "@drogaPodaniaNazwa")]
    pub droga_podania_nazwa: String,

    #[serde(rename = "gatunki", default)]
    pub gatunki: Option<Gatunki>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct Gatunki {
    #[serde(rename = "gatunek", default)]
    pub gatunki: Vec<Gatunek>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct Gatunek {
    #[serde(rename = "@nazwaGatunku")]
    pub nazwa_gatunku: String,

    #[serde(rename = "okresyKarencji", default)]
    pub okresy_karencji: Option<OkresyKarencji>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct OkresyKarencji {
    #[serde(rename = "okresKarencji", default)]
    pub okresy: Vec<OkresKarencji>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct OkresKarencji {
    #[serde(rename = "@jednostkaMiary")]
    pub jednostka_miary: String,

    #[serde(rename = "@nazwaTkanki")]
    pub nazwa_tkanki: String,

    #[serde(rename = "@wartoscMiary")]
    pub wartosc_miary: String,
}

#[derive(Debug, Deserialize, Clone)]
pub struct SubstancjeCzynne {
    #[serde(rename = "substancjaCzynna", default)]
    pub substancje: Vec<SubstancjaCzynna>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct SubstancjaCzynna {
    #[serde(rename = "@nazwaSubstancji")]
    pub nazwa_substancji: String,

    #[serde(rename = "@iloscSubstancji")]
    pub ilosc_substancji: String,

    #[serde(rename = "@jednostkaMiaryIlosciSubstancji")]
    pub jednostka_miary_ilosci_substancji: String,

    #[serde(rename = "@iloscPreparatu")]
    pub ilosc_preparatu: Option<String>,

    #[serde(rename = "@jednostkaMiaryIlosciPreparatu")]
    pub jednostka_miary_ilosci_preparatu: Option<String>,

    #[serde(rename = "@innyOpisIlosci")]
    pub inny_opis_ilosci: Option<String>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct Opakowania {
    #[serde(rename = "opakowanie", default)]
    pub opakowania: Vec<Opakowanie>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct Opakowanie {
    #[serde(rename = "@kodGTIN")]
    pub kod_gtin: Option<String>,

    #[serde(rename = "@kategoriaDostepnosci")]
    pub kategoria_dostepnosci: String,

    #[serde(rename = "@skasowane")]
    pub skasowane: String,

    #[serde(rename = "@numerEu")]
    pub numer_eu: Option<String>,

    #[serde(rename = "@dystrybutorRownolegly")]
    pub dystrybutor_rownolegly: Option<String>,

    #[serde(rename = "@id")]
    pub id: u64,

    #[serde(rename = "jednostkiOpakowania", default)]
    pub jednostki_opakowania: Option<JednostkiOpakowania>,

    #[serde(rename = "zgodyPrezesa", default)]
    pub zgody_prezesa: Option<ZgodyPrezesa>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct JednostkiOpakowania {
    #[serde(rename = "jednostkaOpakowania", default)]
    pub jednostki: Vec<JednostkaOpakowania>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct JednostkaOpakowania {
    #[serde(rename = "@liczbaOpakowan")]
    pub liczba_opakowan: Option<String>,

    #[serde(rename = "@rodzajOpakowania")]
    pub rodzaj_opakowania: Option<String>,

    #[serde(rename = "@pojemnosc")]
    pub pojemnosc: Option<String>,

    #[serde(rename = "@jednostkaPojemnosci")]
    pub jednostka_pojemnosci: Option<String>,

    #[serde(rename = "@informacjeDodatkowe")]
    pub informacje_dodatkowe: Option<String>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct ZgodyPrezesa {
    #[serde(rename = "zgodaPrezesa", default)]
    pub zgody: Vec<ZgodaPrezesa>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct ZgodaPrezesa {
    #[serde(rename = "nrZgodyPrezesa")]
    pub nr_zgody_prezesa: String,

    #[serde(rename = "GTINZagraniczne", default)]
    pub gtin_zagraniczne: Option<GTINZagraniczne>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct GTINZagraniczne {
    #[serde(rename = "GTINZagraniczny", default)]
    pub gtin_zagraniczne: Vec<GTINZagraniczny>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct GTINZagraniczny {
    #[serde(rename = "@numer")]
    pub numer: String,
}

#[derive(Debug, Deserialize, Clone)]
pub struct DaneOWytworcy {
    #[serde(rename = "wytworcy", default)]
    pub wytworcy: Vec<Wytworca>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct Wytworca {
    #[serde(rename = "@nazwaWytworcyImportera")]
    pub nazwa_wytworcy_importera: Option<String>,

    #[serde(rename = "@krajWytworcyImportera")]
    pub kraj_wytworcy_importera: Option<String>,

    #[serde(rename = "@krajEksportu")]
    pub kraj_eksportu: Option<String>,

    #[serde(rename = "@podmiotOdpowiedzialnywKrajuEksportu")]
    pub podmiot_odpowiedzialny_w_kraju_eksportu: Option<String>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct MaterialyEdukacyjne {
    #[serde(rename = "dlaPacjenta", default)]
    pub dla_pacjenta: Option<MaterialyDla>,

    #[serde(rename = "dlaMedyka", default)]
    pub dla_medyka: Option<MaterialyDla>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct MaterialyDla {
    #[serde(rename = "materialEdukacyjny", default)]
    pub materialy: Vec<MaterialEdukacyjny>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct MaterialEdukacyjny {
    #[serde(rename = "@material")]
    pub material: String,

    #[serde(rename = "@nazwaMaterialu")]
    pub nazwa_materialu: String,
}
